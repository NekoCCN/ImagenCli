"""Apply M1 remote/save facts without retrying creation or publishing bad turns."""

from typing import Any

from imagen_cli.storage.repositories import Repositories


class OutcomeService:
    def __init__(self, repositories: Repositories) -> None:
        self.repositories = repositories

    def _job(self, job_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        job = self.repositories.get("jobs", job_id)
        if job is None:
            raise KeyError(job_id)
        turn = self.repositories.get("turns", job["turn_id"])
        if turn is None or turn.get("job_id") != job_id:
            raise ValueError("job must belong to its producer turn")
        return job, turn

    def _artifacts(self, job_id: str, turn_id: str) -> list[dict[str, Any]]:
        refs = self.repositories.list("artifact_references", turn_id=turn_id, role="output")
        rows = [self.repositories.get("artifacts", ref["artifact_id"]) for ref in refs]
        return [
            row
            for row in rows
            if row
            and row["save_state"] == "published"
            and row["producer_job_id"] == job_id
            and row["producer_turn_id"] == turn_id
        ]

    def _result(
        self,
        status: str,
        code: int,
        artifacts: list[dict[str, Any]],
        error_type: str | None = None,
        next_actions: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": status,
            "exit_code": code,
            "artifacts": artifacts,
            "error_type": error_type,
            "next_actions": next_actions or [],
        }

    def complete(self, job_id: str, *, actual_count: int) -> dict[str, Any]:
        if type(actual_count) is not int or actual_count < 0:
            raise ValueError("invalid actual count")
        with self.repositories.transaction():
            job, turn = self._job(job_id)
            artifacts = self._artifacts(job_id, turn["id"])
            saved = len(artifacts)
            if saved > actual_count:
                raise ValueError("saved outputs exceed actual count")
            requested = job.get("requested_count", 0)
            if actual_count == 0:
                status, code, state, local, creation, error = (
                    "failed",
                    7,
                    "failed",
                    "not_started",
                    "failed",
                    "provider_error",
                )
                actions = []
            elif saved == 0:
                status, code, state, local, creation, error = (
                    "save_failed",
                    7,
                    "failed",
                    "failed",
                    "failed",
                    "save_failed",
                )
                actions = ["fetch", "reconcile"]
            elif saved < actual_count or actual_count < requested:
                status, code, state, local, creation, error = (
                    "partial_success",
                    9,
                    "failed",
                    "partial",
                    "partial_success",
                    "partial_failure",
                )
                actions = ["fetch", "reconcile"]
            else:
                status, code, state, local, creation, error = (
                    "succeeded",
                    0,
                    "succeeded",
                    "saved",
                    "succeeded",
                    None,
                )
                actions = []
            self.repositories.update(
                "jobs",
                job_id,
                {
                    "state": state,
                    "remote_state": "succeeded",
                    "local_save_state": local,
                    "actual_count": actual_count,
                },
            )
            self.repositories.update("turns", turn["id"], {"creation_state": creation})
            if status == "succeeded":
                publication = turn.get("publication_state")
                if publication == "published":
                    return self._result(status, code, artifacts)
                if publication == "conflict" or not self.repositories.publish_turn(
                    turn["session_id"],
                    turn["id"],
                    turn.get("expected_session_version", 0),
                ):
                    return self._result("conflict", 5, artifacts, "conflict", ["fork"])
            return self._result(status, code, artifacts, error, actions)

    def fail(
        self,
        job_id: str,
        *,
        error_type: str,
        accepted_maybe: bool = False,
    ) -> dict[str, Any]:
        with self.repositories.transaction():
            job, turn = self._job(job_id)
            if job.get("remote_state") == "succeeded":
                return self.complete(job_id, actual_count=job["actual_count"])
            status = "unknown" if accepted_maybe else "failed"
            self.repositories.update(
                "jobs",
                job_id,
                {
                    "state": status,
                    "remote_state": status,
                    "accepted_maybe": accepted_maybe,
                },
            )
            self.repositories.update("turns", turn["id"], {"creation_state": status})
            return self._result(
                status,
                6 if accepted_maybe else 7,
                self._artifacts(job_id, turn["id"]),
                error_type,
            )
