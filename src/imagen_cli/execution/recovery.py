"""Durable pre-network intent and remote-fact recording; no automatic retry."""

import json
from typing import Any

from imagen_cli.application.cancel_service import executor_id
from imagen_cli.application.cost_service import CostService
from imagen_cli.application.job_state import JobState
from imagen_cli.providers.base import ImmediateResult, ValidatedRequest
from imagen_cli.storage.repositories import Repositories

OUTCOME_UNKNOWN = "outcome_unknown"


def record_intent(
    repositories: Repositories, request: ValidatedRequest, requested_count: int | None,
) -> None:
    """Commit the send intent before any provider call so a crash is never a retry."""
    if request.authorized is not True:
        raise ValueError("request must be explicitly authorized")
    if repositories.connection.in_transaction:
        raise ValueError("dispatch requires an independently committed transaction")
    with repositories.transaction():
        job = repositories.get("jobs", request.job_id, include_private=True)
        if job is None:
            raise KeyError(request.job_id)
        turn = repositories.get("turns", job["turn_id"])
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
        JobState(repositories).record(
            request.job_id, event="dispatch_intent_recorded",
            state="running", dispatch_state="intent_recorded",
        )
        updates: dict[str, Any] = {
            "cost": CostService.normalize(None).model_dump(mode="json"),
        }
        if (identity := executor_id()) is not None:
            updates["executor_id"] = identity
        repositories.update("jobs", request.job_id, updates)
        repositories.update("turns", turn["id"], {"creation_state": "running"})


def record_response(
    repositories: Repositories, job_id: str, result: ImmediateResult, private_ref: dict[str, Any],
) -> None:
    with repositories.transaction():
        JobState(repositories).record(
            job_id, event="response_received",
            dispatch_state="response_received", remote_state="succeeded",
        )
        repositories.update(
            "jobs", job_id,
            {
                "actual_count": result.actual_count,
                "cost": CostService.normalize(result.usage).model_dump(mode="json"),
                "private_locator_ref": json.dumps(private_ref),
            },
        )
