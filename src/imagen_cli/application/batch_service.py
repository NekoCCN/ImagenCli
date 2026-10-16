"""Bounded batch orchestration over ordinary child jobs; children are never rewritten."""

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from imagen_cli.application.batch_summary import summarize
from imagen_cli.domain.contracts import CreationInput
from imagen_cli.domain.primitive import new_id
from imagen_cli.storage.repositories import Repositories

DEFAULT_CONCURRENCY = 2
MAX_CONCURRENCY = 8
MAX_REQUESTS = 100

Prepare = Callable[[CreationInput], Any]
RunChild = Callable[[Any, int], str]
ProviderLimit = Callable[[Any], int | None]


def effective_concurrency(
    requested: int | None, provider_limits: Sequence[int | None]
) -> int:
    """Requested concurrency capped at 8 and never above a declared provider limit."""
    value = DEFAULT_CONCURRENCY if requested is None else requested
    if type(value) is not int or not 1 <= value <= MAX_CONCURRENCY:
        raise ValueError(f"concurrency must be 1-{MAX_CONCURRENCY}")
    limits: list[int] = []
    for limit in provider_limits:
        if limit is None:
            continue
        if type(limit) is not int or limit < 1:
            raise ValueError("provider limit must be positive")
        limits.append(limit)
    return min([value, *limits])


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _rows(repositories: Repositories, table: str, **filters: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    while True:
        page = repositories.list(table, limit=1000, offset=len(rows), **filters)
        rows.extend(page)
        if len(page) < 1000:
            return rows


class BatchService:
    def __init__(
        self,
        repositories: Repositories,
        *,
        prepare: Prepare | None = None,
        run: RunChild | None = None,
        provider_limit: ProviderLimit | None = None,
    ) -> None:
        self.repositories = repositories
        self.prepare = prepare or (lambda creation: creation)
        self.run = run
        self.provider_limit = provider_limit

    def submit(
        self,
        requests: Sequence[CreationInput | Mapping[str, Any]],
        concurrency: int | None = None,
    ) -> tuple[str, dict[str, Any]]:
        if isinstance(requests, (str, bytes)) or not 1 <= len(requests) <= MAX_REQUESTS:
            raise ValueError(f"batch requires 1-{MAX_REQUESTS} requests")
        creations = [
            request
            if isinstance(request, CreationInput)
            else CreationInput.model_validate(request)
            for request in requests
        ]
        # Every child is validated and planned offline before any provider call.
        prepared = [self.prepare(creation) for creation in creations]
        limits = [
            self.provider_limit(item) if self.provider_limit is not None else None
            for item in prepared
        ]
        effective = effective_concurrency(concurrency, limits)
        if self.run is None:
            raise ValueError("batch execution is unavailable")
        with ThreadPoolExecutor(max_workers=effective) as pool:
            futures = [
                pool.submit(self.run, item, index) for index, item in enumerate(prepared)
            ]
            # Read in request order so child identities stay ordered regardless of finish order.
            job_ids = [future.result() for future in futures]
        if len(set(job_ids)) != len(job_ids):
            raise ValueError("batch child jobs must be unique")
        identity = new_id("bat")
        with self.repositories.transaction():
            summary = summarize(self._facts(job_ids))
            self.repositories.insert(
                "batches",
                {
                    "id": identity,
                    "child_job_ids": job_ids,
                    "concurrency": effective,
                    "summary_state": summary["state"],
                    "created_at": _now(),
                },
            )
        return self._status(summary["state"]), self._data(identity)

    def facts(self, batch_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        batch = self.repositories.get("batches", batch_id)
        if batch is None:
            raise KeyError(batch_id)
        return batch, self._facts(batch.get("child_job_ids", []))

    def get(self, batch_id: str) -> tuple[str, dict[str, Any]]:
        if self.repositories.get("batches", batch_id) is None:
            return "not_found", {}
        return "succeeded", self._data(batch_id)

    def wait(self, batch_id: str, *, timeout: int = 300) -> tuple[str, dict[str, Any]]:
        if type(timeout) is not int or not 1 <= timeout <= 86400:
            raise ValueError("invalid timeout")
        if self.repositories.get("batches", batch_id) is None:
            return "not_found", {}
        import time

        deadline = time.monotonic() + timeout
        while True:
            data = self._data(batch_id)
            state = data["summary"]["state"]
            if state != "running" or time.monotonic() >= deadline:
                break
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        state = data["summary"]["state"]
        if state == "running":
            return "wait_timeout", data
        return self._status(state), data

    def _child(self, job_id: str) -> dict[str, Any]:
        job = self.repositories.get("jobs", job_id)
        if job is None:
            raise ValueError("batch child job is missing")
        saved = sum(
            1
            for row in _rows(self.repositories, "artifacts", producer_job_id=job_id)
            if row.get("save_state") == "published"
        )
        return {
            "job_id": job_id,
            "state": job.get("state"),
            "remote_state": job.get("remote_state"),
            "local_save_state": job.get("local_save_state"),
            "actual_count": job.get("actual_count", 0),
            "requested_count": job.get("requested_count", 0),
            "saved_artifact_count": saved,
        }

    def _facts(self, job_ids: Sequence[str]) -> list[dict[str, Any]]:
        return [self._child(job_id) for job_id in job_ids]

    def _data(self, batch_id: str) -> dict[str, Any]:
        batch, children = self.facts(batch_id)
        summary = summarize(children)
        self.repositories.update("batches", batch_id, {"summary_state": summary["state"]})
        events: list[dict[str, Any]] = []
        for child in children:
            for row in _rows(self.repositories, "events", job_id=child["job_id"]):
                events.append(
                    {
                        "job_id": row["job_id"],
                        "job_seq": row["job_seq"],
                        "event_type": row["event_type"],
                        "timestamp": row["timestamp"],
                        "safe_payload": row.get("safe_payload", {}),
                    }
                )
        return {
            "batch": self.repositories.get("batches", batch_id),
            "summary": summary,
            "children": children,
            "events": events,
        }

    @staticmethod
    def _status(state: str) -> str:
        if state == "running":
            return "wait_timeout"
        return state
