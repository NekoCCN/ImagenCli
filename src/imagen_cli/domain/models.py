"""Persistent entity shapes and local invariants.

Cross-record uniqueness, foreign keys and atomic state transitions belong to the
repository transaction; explicit relationship checks are provided for its callers.
"""

import re
from datetime import date, datetime
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import AfterValidator, ConfigDict, Field, model_validator

from .contracts import JsonObject, enum_value
from .enums import (
    ApprovalState,
    ArtifactRole,
    ArtifactSaveState,
    CapabilityStatus,
    CreationState,
    DispatchState,
    JobState,
    LocalSaveState,
    Operation,
    PublicationState,
    RemoteState,
)
from .primitive import (
    ApprovalId,
    ArtifactId,
    BatchId,
    ContextId,
    EventId,
    JobId,
    NonEmptyString,
    NonNegativeInt,
    PlanId,
    PositiveInt,
    SessionId,
    Sha256,
    StrictModel,
    TurnId,
    UtcTimestamp,
    WorkerId,
)


def managed_path(value: str) -> str:
    if (
        not value
        or "\x00" in value
        or "\\" in value
        or value.startswith("/")
        or re.match(r"^[A-Za-z]:", value)
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError("path must be a normal relative managed path")
    return value


def endpoint_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("endpoint must be an HTTP(S) URL without credentials, query or fragment")
    _ = parsed.port  # Access validates malformed or out-of-range ports.
    return value


ManagedPath = Annotated[str, AfterValidator(managed_path)]
EndpointUrl = Annotated[str, AfterValidator(endpoint_url)]


class Session(StrictModel):
    id: SessionId
    name: NonEmptyString
    intent: str
    default_profile_id: NonEmptyString | None = None
    default_model: NonEmptyString | None = None
    current_turn_id: TurnId | None = None
    version: NonNegativeInt
    created_at: UtcTimestamp
    deleted_at: UtcTimestamp | None = None
    origin_session_id: SessionId | None = None
    origin_turn_id: TurnId | None = None

    def validate_current_turn(self, turn: "Turn") -> None:
        if (
            turn.id != self.current_turn_id
            or turn.session_id != self.id
            or turn.creation_state != CreationState.SUCCEEDED
            or turn.publication_state != PublicationState.PUBLISHED
        ):
            raise ValueError("current turn must be a published complete success in this session")


class Turn(StrictModel):
    id: TurnId
    session_id: SessionId
    parent_turn_id: TurnId | None = None
    instruction: NonEmptyString
    operation: Annotated[Operation, enum_value(Operation)]
    resolved_profile_snapshot: JsonObject
    model: NonEmptyString
    parameters: JsonObject
    input_manifest: list[JsonObject]
    expected_session_version: NonNegativeInt
    job_id: JobId
    creation_state: Annotated[CreationState, enum_value(CreationState)]
    publication_state: Annotated[PublicationState, enum_value(PublicationState)]
    created_at: UtcTimestamp
    finished_at: UtcTimestamp | None = None

    @model_validator(mode="after")
    def local_relations(self) -> Self:
        if self.parent_turn_id == self.id:
            raise ValueError("turn cannot be its own parent")
        if (
            self.publication_state != PublicationState.NOT_ATTEMPTED
            and self.creation_state != CreationState.SUCCEEDED
        ):
            raise ValueError("only complete success can be published or conflict")
        if self.finished_at is not None and self.finished_at < self.created_at:
            raise ValueError("turn cannot finish before creation")
        return self

    def validate_parent(self, parent: "Turn") -> None:
        if parent.id != self.parent_turn_id or parent.session_id != self.session_id:
            raise ValueError("parent must be the referenced turn in this session")


class Artifact(StrictModel):
    id: ArtifactId
    producer_turn_id: TurnId | None = None
    producer_job_id: JobId | None = None
    media_type: NonEmptyString | None = None
    width: PositiveInt | None = None
    height: PositiveInt | None = None
    byte_size: NonNegativeInt | None = None
    sha256: Sha256 | None = None
    managed_relpath: ManagedPath
    save_state: Annotated[ArtifactSaveState, enum_value(ArtifactSaveState)]
    origin_manifest: JsonObject
    created_at: UtcTimestamp

    @model_validator(mode="after")
    def published_metadata(self) -> Self:
        if (
            self.producer_turn_id is None
            and self.producer_job_id is None
            and not self.origin_manifest
        ):
            raise ValueError("imported artifacts require origin metadata")
        if self.save_state == ArtifactSaveState.PUBLISHED and any(
            value is None
            for value in (
                self.media_type,
                self.width,
                self.height,
                self.byte_size,
                self.sha256,
            )
        ):
            raise ValueError("published artifact requires all measured media fields")
        return self


class ArtifactReference(StrictModel):
    session_id: SessionId
    turn_id: TurnId
    artifact_id: ArtifactId
    role: Annotated[ArtifactRole, enum_value(ArtifactRole)]
    index: NonNegativeInt


class CancellationFact(StrictModel):
    timestamp: UtcTimestamp
    source: NonEmptyString


class Cost(StrictModel):
    estimate: Annotated[float, Field(ge=0)] | None
    actual: Annotated[float, Field(ge=0)] | None
    currency: NonEmptyString
    unknown_reason: NonEmptyString | None = None

    @model_validator(mode="after")
    def unknown_has_reason(self) -> Self:
        if (self.estimate is None or self.actual is None) and self.unknown_reason is None:
            raise ValueError("unknown costs require a reason")
        return self


class Job(StrictModel):
    id: JobId
    turn_id: TurnId
    batch_id: BatchId | None = None
    plan_id: PlanId | None = None
    retry_of_job_id: JobId | None = None
    state: Annotated[JobState, enum_value(JobState)]
    dispatch_state: Annotated[DispatchState, enum_value(DispatchState)]
    remote_state: Annotated[RemoteState, enum_value(RemoteState)]
    local_save_state: Annotated[LocalSaveState, enum_value(LocalSaveState)]
    requested_count: PositiveInt
    actual_count: NonNegativeInt
    accepted_maybe: bool
    remote_id: NonEmptyString | None = None
    private_locator_ref: ManagedPath | None = None
    executor_id: WorkerId | None = None
    lease_token: NonEmptyString | None = None
    lease_expires_at: UtcTimestamp | None = None
    cancel_intent: CancellationFact | None = None
    cancel_local: CancellationFact | None = None
    cancel_remote: CancellationFact | None = None
    cost: Cost | None = None
    created_at: UtcTimestamp
    updated_at: UtcTimestamp


class Batch(StrictModel):
    id: BatchId
    child_job_ids: Annotated[list[JobId], Field(min_length=1)]
    concurrency: PositiveInt
    summary_state: Annotated[CreationState, enum_value(CreationState)]
    created_at: UtcTimestamp

    @model_validator(mode="after")
    def unique_children(self) -> Self:
        if len(self.child_job_ids) != len(set(self.child_job_ids)):
            raise ValueError("batch child jobs must be unique and ordered")
        return self


class CapabilitySource(StrictModel):
    url: EndpointUrl
    date: str
    locator: NonEmptyString

    @model_validator(mode="after")
    def valid_date(self) -> Self:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", self.date):
            raise ValueError("source date must be YYYY-MM-DD")
        date.fromisoformat(self.date)
        return self


class CapabilityRecord(StrictModel):
    id: NonEmptyString
    provider: NonEmptyString
    family: NonEmptyString
    model: NonEmptyString
    model_revision: NonEmptyString
    operation: Annotated[Operation, enum_value(Operation)]
    support_status: Annotated[CapabilityStatus, enum_value(CapabilityStatus)]
    parameter_schema: JsonObject
    semantic_mapping: JsonObject
    input_limits: JsonObject
    transport_kind: Literal["sync", "remote_async", "stream"]
    continuation_strategy: Literal["none", "explicit_images", "native_parts", "remote_conversation"]
    idempotency_contract: JsonObject
    cancel_contract: JsonObject
    sources: Annotated[list[CapabilitySource], Field(min_length=1)]
    checked_at: UtcTimestamp
    offline_evidence: list[JsonObject]
    live_evidence: list[JsonObject]
    digest: Sha256

    @model_validator(mode="after")
    def tested_requires_live(self) -> Self:
        if self.support_status == CapabilityStatus.TESTED and not any(self.live_evidence):
            raise ValueError("tested capability requires live evidence")
        return self


class ProviderProfile(StrictModel):
    id: NonEmptyString
    name: NonEmptyString
    provider: NonEmptyString
    family: NonEmptyString
    base_url: EndpointUrl
    model_default: NonEmptyString
    credential_ref: NonEmptyString
    trust_policy: JsonObject
    revision: PositiveInt


class NativeContext(StrictModel):
    id: ContextId
    provider: NonEmptyString
    family: NonEmptyString
    profile_identity: NonEmptyString
    model: NonEmptyString
    format_version: PositiveInt
    private_relpath: ManagedPath
    sha256: Sha256
    expires_at: UtcTimestamp | None = None
    availability: Literal["available", "missing", "expired"]
    source_turn_id: TurnId


class ExecutionPlan(StrictModel):
    model_config = ConfigDict(frozen=True)

    id: PlanId
    session_id: SessionId
    parent_turn_id: TurnId | None = None
    expected_session_version: NonNegativeInt
    target_snapshot: JsonObject
    operation: Annotated[Operation, enum_value(Operation)]
    model: NonEmptyString
    normalized_parameters: JsonObject
    prompt_snapshot: NonEmptyString
    input_hashes: list[Sha256]
    portable_context: JsonObject
    capability_digest: Sha256
    context_loss: list[str]
    plan_digest: Sha256
    created_at: UtcTimestamp
    expires_at: UtcTimestamp

    @model_validator(mode="after")
    def valid_lifetime(self) -> Self:
        if datetime.fromisoformat(self.expires_at) <= datetime.fromisoformat(self.created_at):
            raise ValueError("plan expiry must follow creation")
        return self


class ApprovalContext(StrictModel):
    id: ApprovalId
    plan_id: PlanId
    plan_digest: Sha256
    state: Annotated[ApprovalState, enum_value(ApprovalState)]
    approved_at: UtcTimestamp | None = None
    execution_job_id: JobId | None = None
    expires_at: UtcTimestamp

    @model_validator(mode="after")
    def approval_facts(self) -> Self:
        if (
            self.state in {ApprovalState.APPROVED, ApprovalState.CONSUMED}
            and self.approved_at is None
        ):
            raise ValueError("approval requires approval timestamp")
        if self.state == ApprovalState.CONSUMED and self.execution_job_id is None:
            raise ValueError("consumed approval requires execution job")
        if self.execution_job_id is not None and self.state != ApprovalState.CONSUMED:
            raise ValueError("only consumed approval has execution job")
        if self.approved_at is not None and datetime.fromisoformat(
            self.approved_at
        ) >= datetime.fromisoformat(self.expires_at):
            raise ValueError("approval must precede expiry")
        return self


class ExecutionEvent(StrictModel):
    id: EventId
    job_id: JobId
    job_seq: PositiveInt
    event_type: NonEmptyString
    timestamp: UtcTimestamp
    safe_payload: JsonObject


class Worker(StrictModel):
    id: WorkerId
    pid: PositiveInt
    boot_identity: NonEmptyString
    state: Literal["starting", "running", "stopping", "stopped", "failed"]
    heartbeat_at: UtcTimestamp
    start_nonce: NonEmptyString
