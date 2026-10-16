"""Local job observation handlers; no provider or creation dependency."""

import time
from typing import Any

from imagen_cli.application.artifact_service import ArtifactService


def _data(service: ArtifactService, job_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    job = service.repositories.get("jobs", job_id)
    if job is None:
        raise KeyError(job_id)
    turn = service.repositories.get("turns", job["turn_id"])
    if turn is None or turn.get("job_id") != job_id:
        raise ValueError("job must belong to its producer turn")
    return job, turn


def _artifacts(
    service: ArtifactService, job_id: str, *, reconcile: bool = False
) -> list[dict[str, Any]]:
    job, turn = _data(service, job_id)
    refs = service.repositories.list("artifact_references", turn_id=turn["id"], role="output")
    if reconcile:
        known = {ref["artifact_id"] for ref in refs}
        refs.extend(
            {"artifact_id": row["id"]}
            for row in service.repositories.list("artifacts", producer_job_id=job_id)
            if row["id"] not in known
        )
    result = []
    for ref in refs:
        row = service.repositories.get("artifacts", ref["artifact_id"])
        if not row or row.get("producer_job_id") != job_id:
            continue
        if reconcile:
            try:
                row = service.reconcile(row["id"])
            except (OSError, ValueError):
                continue
        try:
            path = service.artifact_store.get(row["id"])
        except (FileNotFoundError, OSError, ValueError):
            continue
        if path is None:
            continue
        result.append({**row, "path": str(path)})
    return result


def _result(job: dict[str, Any], artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "job": job,
        "artifacts": artifacts,
        "next_actions": [{"command": "job get " + job["id"], "creates_paid_request": False}],
    }


def _outcome(service: ArtifactService, job: dict[str, Any]) -> str:
    actual = job.get("actual_count", 0)
    saved = len(_artifacts(service, job["id"]))
    if job.get("state") == "succeeded" and saved < actual:
        return "save_failed" if saved == 0 else "partial_success"
    return str(job["state"])


def get_(service: ArtifactService, job_id: str) -> tuple[str, dict[str, Any]]:
    try:
        job, _ = _data(service, job_id)
    except KeyError:
        return "not_found", {}
    return "succeeded", _result(job, _artifacts(service, job_id))


def cancel_(
    service: ArtifactService, job_id: str, *, scope: str,
) -> tuple[str, dict[str, Any]]:
    if scope not in {"wait", "local", "remote"}:
        raise ValueError("invalid cancellation scope")
    try:
        job, _ = _data(service, job_id)
    except KeyError:
        return "not_found", {}
    if scope == "local":
        from imagen_cli.application.cancel_service import cancel_local

        status = cancel_local(service.repositories, job_id)
        return status, _result(job, _artifacts(service, job_id))
    if scope != "wait":
        # No provider declares a verified remote cancellation contract yet.
        return "unsupported_capability", _result(job, _artifacts(service, job_id))
    service.repositories.append_event(job_id, "wait_stopped", {"source": "job cancel"})
    return "succeeded", _result(job, _artifacts(service, job_id))


def wait_(
    service: ArtifactService, job_id: str, *, timeout: int = 300
) -> tuple[str, dict[str, Any]]:
    if type(timeout) is not int or not 1 <= timeout <= 86400:
        raise ValueError("invalid timeout")
    try:
        job, _ = _data(service, job_id)
    except KeyError:
        return "not_found", {}
    deadline = time.monotonic() + timeout
    connection = service.repositories.connection
    observed_seq = connection.execute(
        "SELECT COALESCE(MAX(job_seq),0) FROM events WHERE job_id=?", (job_id,),
    ).fetchone()[0]
    while job["state"] in {"queued", "running"}:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.05, remaining))
        job, _ = _data(service, job_id)
        stopped = connection.execute(
            "SELECT 1 FROM events WHERE job_id=? AND job_seq>? "
            "AND event_type='wait_stopped' LIMIT 1", (job_id, observed_seq),
        ).fetchone()
        if stopped:
            return "canceled", _result(job, _artifacts(service, job_id))
    artifacts = _artifacts(service, job_id)
    if job["state"] in {"queued", "running"}:
        return "wait_timeout", _result(job, artifacts)
    return _outcome(service, job), _result(job, artifacts)


def fetch_(service: ArtifactService, job_id: str) -> tuple[str, dict[str, Any]]:
    try:
        job, _ = _data(service, job_id)
    except KeyError:
        return "not_found", {}
    artifacts = _artifacts(service, job_id)
    local_rows = service.repositories.list("artifacts", producer_job_id=job_id)
    if len(artifacts) < max(len(local_rows), job.get("actual_count", 0)) and artifacts:
        return "partial_success", _result(job, artifacts)
    if len(artifacts) < max(len(local_rows), job.get("actual_count", 0)):
        return "save_failed", _result(job, artifacts)
    if not artifacts:
        return "unsupported_capability", {
            "job": job,
            "artifacts": [],
            "next_actions": [{"command": "job reconcile " + job_id, "creates_paid_request": False}],
        }
    return "succeeded", _result(job, artifacts)


def reconcile_(service: ArtifactService, job_id: str) -> tuple[str, dict[str, Any]]:
    try:
        job, _ = _data(service, job_id)
    except KeyError:
        return "not_found", {}
    artifacts = _artifacts(service, job_id, reconcile=True)
    local_rows = service.repositories.list("artifacts", producer_job_id=job_id)
    if len(artifacts) < max(len(local_rows), job.get("actual_count", 0)) and artifacts:
        return "partial_success", _result(job, artifacts)
    if len(artifacts) < max(len(local_rows), job.get("actual_count", 0)):
        return "save_failed", _result(job, artifacts)
    if not artifacts:
        return "unsupported_capability", {
            "job": job,
            "artifacts": [],
            "next_actions": [{"command": "job get " + job_id, "creates_paid_request": False}],
        }
    return "succeeded", _result(job, artifacts)
