"""Machine envelopes and creation input matching the version-one JSON contracts."""

from typing import Annotated, Any, Literal, Self

from pydantic import BeforeValidator, Field, JsonValue, model_validator

from .enums import ErrorType, ResultStatus
from .errors import exit_code_for
from .primitive import NonEmptyString, PositiveInt, StrictModel, UtcTimestamp


def enum_value(enum: Any) -> Any:
    def validate(value: Any) -> Any:
        if not isinstance(value, str):
            raise ValueError("enum must be a string")
        return enum(value)

    return BeforeValidator(validate)


SchemaVersion = Annotated[int, Field(strict=True, ge=1, le=1)]
ContractJobId = Annotated[str, Field(pattern=r"^job_[0-9a-f]{32}$")]
AssetInput = Annotated[str, Field(pattern=r"^(artifact:art_[0-9a-f]{32}|file:.+)$")]
JsonObject = dict[str, JsonValue]


class NextAction(StrictModel):
    command: NonEmptyString
    creates_paid_request: bool


class MachineError(StrictModel):
    type: Annotated[ErrorType, enum_value(ErrorType)]
    message: NonEmptyString
    retryable: bool
    accepted_maybe: bool
    next_actions: list[NextAction]


class Result(StrictModel):
    schema_version: SchemaVersion
    ok: bool
    status: Annotated[ResultStatus, enum_value(ResultStatus)]
    data: JsonObject
    error: MachineError | None

    @model_validator(mode="after")
    def consistent_outcome(self) -> Self:
        success = self.status in {"succeeded", "accepted", "approved", "planned"}
        if self.ok != success or (self.error is None) != self.ok:
            raise ValueError("ok, status and error must agree")
        return self

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else exit_code_for(self.status)


class InvocationEvent(StrictModel):
    schema_version: SchemaVersion
    seq: PositiveInt
    event: Literal["started", "progress", "job_updated", "artifact_saved", "terminal"]
    timestamp: UtcTimestamp
    job_id: ContractJobId | None
    data: JsonObject
    result: Result | None = None

    @model_validator(mode="before")
    @classmethod
    def terminal_shape(cls, value: Any) -> Any:
        if isinstance(value, dict):
            terminal = value.get("event") == "terminal"
            if terminal != ("result" in value) or (terminal and value["result"] is None):
                raise ValueError("only terminal events require result")
            if (
                value.get("event") in {"job_updated", "artifact_saved"}
                and value.get("job_id") is None
            ):
                raise ValueError("job events require job_id")
        return value


class CreationInput(StrictModel):
    schema_version: SchemaVersion
    operation: Literal["generate", "edit"]
    session: NonEmptyString
    prompt: Annotated[str, Field(min_length=1, pattern=r"\S")]
    profile: NonEmptyString | None = None
    model: NonEmptyString | None = None
    inputs: Annotated[list[AssetInput], Field(max_length=100)] = Field(default_factory=list)
    mask: AssetInput | None = None
    references: Annotated[list[AssetInput], Field(max_length=100)] = Field(default_factory=list)
    count: Annotated[int, Field(strict=True, ge=1, le=100)] = 1
    size: NonEmptyString | None = None
    provider_params: JsonObject = Field(default_factory=dict)
    retry_of_job_id: ContractJobId | None = None
    ack_duplicate_cost: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def optional_does_not_mean_nullable(cls, value: Any) -> Any:
        if isinstance(value, dict):
            for field in (
                "profile",
                "model",
                "mask",
                "size",
                "retry_of_job_id",
                "ack_duplicate_cost",
            ):
                if field in value and value[field] is None:
                    raise ValueError(f"{field} cannot be null")
            retry = "retry_of_job_id" in value
            acknowledgement = "ack_duplicate_cost" in value
            if retry != acknowledgement or (retry and value["ack_duplicate_cost"] is not True):
                raise ValueError("retry requires explicit duplicate-cost acknowledgement")
        return value

    @model_validator(mode="after")
    def edit_requires_inputs(self) -> Self:
        if self.operation == "edit" and not self.inputs:
            raise ValueError("edit requires at least one input")
        return self
