"""Provider-neutral foreground execution with no automatic create retry."""

import json
import re
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Protocol

from imagen_cli.application.artifact_service import ArtifactService
from imagen_cli.application.outcome_service import OutcomeService
from imagen_cli.providers.base import ImmediateResult, ProviderFailure, ValidatedRequest
from imagen_cli.storage.private_state import PrivateStateStore
from imagen_cli.storage.repositories import Repositories


class Adapter(Protocol):
    def create(self, request: ValidatedRequest) -> ImmediateResult: ...


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _json_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(child) for child in value]
    return value


class ForegroundExecutor:
    def __init__(
        self,
        repositories: Repositories,
        adapter: Adapter,
        artifact_service: ArtifactService,
        outcome_service: OutcomeService,
        private_state_store: PrivateStateStore,
    ) -> None:
        self.repositories = repositories
        self.adapter = adapter
        self.artifact_service = artifact_service
        self.outcome_service = outcome_service
        self.private_state_store = private_state_store

    def _intent(self, request: ValidatedRequest, requested_count: int | None) -> None:
        if request.authorized is not True:
            raise ValueError("request must be explicitly authorized")
        if self.repositories.connection.in_transaction:
            raise ValueError("dispatch requires an independently committed transaction")
        with self.repositories.transaction():
            job = self.repositories.get("jobs", request.job_id, include_private=True)
            if job is None:
                raise KeyError(request.job_id)
            turn = self.repositories.get("turns", job["turn_id"])
            if turn is None or turn.get("job_id") != request.job_id:
                raise ValueError("job must belong to its producer turn")
            if job["dispatch_state"] != "not_started":
                raise ValueError("job already dispatched")
            if job["state"] != "queued":
                raise ValueError("job is not queued")
            count = job.get("requested_count") if requested_count is None else requested_count
            if type(count) is not int or count <= 0 or count != job.get("requested_count"):
                raise ValueError("requested_count must match the positive job count")
            if job.get("plan_id") not in {None, request.plan_id}:
                raise ValueError("request plan does not match job")
            self.repositories.update(
                "jobs",
                request.job_id,
                {"state": "running", "dispatch_state": "intent_recorded"},
            )
            self.repositories.update("turns", turn["id"], {"creation_state": "running"})
            self.repositories.append_event(request.job_id, "dispatch_intent_recorded", {})

    def read_private_response(self, job_id: str) -> dict[str, Any]:
        row = self.repositories.get("jobs", job_id, include_private=True)
        if row is None or not row.get("private_locator_ref"):
            raise ValueError("private response is unavailable")
        locator = json.loads(row["private_locator_ref"])
        if not isinstance(locator, dict) or set(locator) != {"private_relpath", "sha256"}:
            raise ValueError("invalid private response locator")
        if (
            locator["private_relpath"] != job_id + ".bin"
            or not isinstance(locator["sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", locator["sha256"]) is None
        ):
            raise ValueError("invalid private response locator")
        raw = self.private_state_store.read(job_id, locator["sha256"])
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("private response must be an object")
        return value

    def execute(
        self, request: ValidatedRequest, requested_count: int | None = None
    ) -> dict[str, Any]:
        """Persist send intent, create once, durably save response, then outcomes."""
        self._intent(request, requested_count)
        try:
            result = self.adapter.create(replace(request, dispatch_intent_recorded=True))
        except ProviderFailure as error:
            return self.outcome_service.fail(
                request.job_id,
                error_type=error.type,
                accepted_maybe=error.accepted_maybe,
            )
        except (Exception, KeyboardInterrupt):
            return self.outcome_service.fail(
                request.job_id,
                error_type="outcome_unknown",
                accepted_maybe=True,
            )
        job = self.repositories.get("jobs", request.job_id)
        if (
            not isinstance(result, ImmediateResult)
            or type(result.requested_count) is not int
            or result.requested_count != job["requested_count"]
            or type(result.actual_count) is not int
            or result.actual_count != len(result.artifacts)
            or [item.index for item in result.artifacts] != list(range(result.actual_count))
            or len({item.id for item in result.artifacts}) != result.actual_count
        ):
            return self.outcome_service.fail(
                request.job_id,
                error_type="provider_error",
                accepted_maybe=True,
            )
        try:
            private = json.dumps(_json_value(result.private_response), allow_nan=False).encode()
            private_ref = self.private_state_store.write(request.job_id, private)
            with self.repositories.transaction():
                self.repositories.update(
                    "jobs",
                    request.job_id,
                    {
                        "dispatch_state": "response_received",
                        "remote_state": "succeeded",
                        "actual_count": result.actual_count,
                        "private_locator_ref": json.dumps(private_ref),
                    },
                )
                self.repositories.append_event(request.job_id, "response_received", {})
        except (Exception, KeyboardInterrupt):
            return self.outcome_service.complete(request.job_id, actual_count=result.actual_count)
        for descriptor in result.artifacts:
            try:
                self.artifact_service.save(
                    request.job_id,
                    descriptor.content,
                    artifact_id=descriptor.id,
                    index=descriptor.index,
                )
            except (OSError, ValueError):
                continue
            except KeyboardInterrupt:
                break
        return self.outcome_service.complete(request.job_id, actual_count=result.actual_count)
