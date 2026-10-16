"""Validated axis transitions with a durable per-job event sequence."""

from typing import Any, cast

from imagen_cli.storage.repositories import Repositories

_AXES = {
    "state": ("queued", "running", "succeeded", "failed", "unknown", "canceled"),
    "dispatch_state": ("not_started", "intent_recorded", "response_received"),
    "remote_state": ("not_submitted", "succeeded", "failed", "unknown"),
    "local_save_state": ("not_started", "partial", "saved", "failed"),
}
_DISPATCH_ORDER = {value: index for index, value in enumerate(_AXES["dispatch_state"])}
_TERMINAL = {"succeeded", "failed", "unknown", "canceled"}


class JobState:
    def __init__(self, repositories: Repositories) -> None:
        self.repositories = repositories

    def record(
        self, job_id: str, *, event: str, payload: dict[str, Any] | None = None, **axes: str,
    ) -> dict[str, Any]:
        unknown = set(axes) - set(_AXES)
        if unknown or not axes:
            raise ValueError("unknown job axis")
        for axis, value in axes.items():
            if value not in _AXES[axis]:
                raise ValueError(f"invalid {axis}")
        with self.repositories.transaction():
            job = self.repositories.get("jobs", job_id)
            if job is None:
                raise KeyError(job_id)
            if job["state"] in _TERMINAL and axes.get("state", job["state"]) != job["state"]:
                raise ValueError("terminal job state is immutable")
            current = job["dispatch_state"]
            if _DISPATCH_ORDER[axes.get("dispatch_state", current)] < _DISPATCH_ORDER[current]:
                raise ValueError("dispatch state cannot move backwards")
            self.repositories.update("jobs", job_id, axes)
            self.repositories.append_event(job_id, event, payload or {})
        return cast(dict[str, Any], self.repositories.get("jobs", job_id))
