"""Provider-neutral foreground execution with no automatic create retry."""

import json
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Protocol

from imagen_cli.application.artifact_service import ArtifactService
from imagen_cli.application.outcome_service import OutcomeService
from imagen_cli.execution.recovery import OUTCOME_UNKNOWN, record_intent, record_response
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
        record_intent(self.repositories, request, requested_count)

    def read_private_response(self, job_id: str) -> dict[str, Any]:
        row = self.repositories.get("jobs", job_id, include_private=True)
        if row is None or not row.get("private_locator_ref"):
            raise ValueError("private response is unavailable")
        return self.private_state_store.read_response(job_id, row["private_locator_ref"])

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
                error_type=OUTCOME_UNKNOWN,
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
            record_response(self.repositories, request.job_id, result, private_ref)
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
