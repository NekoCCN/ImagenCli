"""Persistent local processing jobs; computation never dispatches a provider request."""

from __future__ import annotations

import hashlib
import math
import time
from typing import Any

from imagen_cli.application import postprocess_service, postprocess_worker
from imagen_cli.application.progress import ProgressCallback, report
from imagen_cli.application.session_service import _now
from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.repositories import Repositories

LOCAL_OPERATIONS = {"transform", "superres"}


def submit(repositories: Repositories, store: ArtifactStore, source: str, pipeline: Any, *,
           session_id: str | None = None, timeout: float | None = None,
           budget_approval: str | None = None, asynchronous: bool = False) -> dict[str, Any]:
    steps = postprocess_service.validate(pipeline)
    path = store.get(source)
    row = repositories.get("artifacts", source)
    if row is None or path is None:
        raise ValueError(f"Artifact {source} was not found in this workspace.")
    session_id = session_id or postprocess_service._owner(repositories, source)
    session = repositories.get("sessions", session_id)
    if session is None:
        matches = repositories.list("sessions", name=session_id)
        session = matches[0] if len(matches) == 1 else None
    if session is None or session.get("deleted_at"):
        raise ValueError("destination session is unavailable")
    timeout = (postprocess_worker.default_timeout(
        steps, postprocess_worker.source_pixels(path)
    ) if timeout is None else timeout)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    postprocess_service.budget_preflight(path, steps, budget_approval)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != row["sha256"]:
        raise ValueError("source artifact bytes changed")
    operation = "superres" if any(step["op"] == "superres" for step in steps) else "transform"
    with repositories.transaction():
        from imagen_cli.domain.primitive import new_id

        now = _now()
        turn_id, job_id = new_id("trn"), new_id("job")
        repositories.insert("turns", {
            "id": turn_id, "session_id": session["id"], "parent_turn_id": None,
            "instruction": operation, "operation": operation,
            "resolved_profile_snapshot": {"kind": "local"}, "model": "local",
            "parameters": {"pipeline": steps, "timeout": timeout, "source": source,
                           "source_sha256": digest, "budget_approval": budget_approval},
            "input_manifest": [{"artifact_id": source, "role": "input", "index": 0}],
            "expected_session_version": session["version"], "job_id": None,
            "creation_state": "queued", "publication_state": "not_attempted", "created_at": now,
        })
        job = repositories.insert("jobs", {
            "id": job_id, "turn_id": turn_id, "state": "queued", "dispatch_state": "not_started",
            "remote_state": "not_submitted", "local_save_state": "not_started",
            "requested_count": 1, "actual_count": 0, "accepted_maybe": False,
            "created_at": now, "updated_at": now, "worker_queued": asynchronous,
            "progress": {"stage": "queued", "completed": 0, "total": None,
                         "unit": None, "operation": operation},
        })
        repositories.update("turns", turn_id, {"job_id": job_id})
        repositories.insert("artifact_references", {
            "session_id": session["id"], "turn_id": turn_id, "artifact_id": source,
            "role": "input", "index": 0,
        })
        return dict(job)


def cancel(repositories: Repositories, job_id: str) -> str:
    with repositories.transaction():
        job = repositories.get("jobs", job_id)
        if job is None:
            raise KeyError(job_id)
        if job["state"] not in {"queued", "running"}:
            return "conflict"
        fact = {"state": "requested", "timestamp": _now()}
        changes: dict[str, Any] = {"cancel_intent": fact}
        if job["state"] == "queued":
            changes.update({"state": "canceled", "cancel_local": fact, "updated_at": _now()})
            repositories.update("turns", job["turn_id"],
                                {"creation_state": "canceled", "finished_at": _now()})
        repositories.update("jobs", job_id, changes)
        repositories.append_event(job_id, "cancel_intent", {"scope": "local"})
    return "succeeded"


def run(repositories: Repositories, store: ArtifactStore, job_id: str, *,
        progress: ProgressCallback | None = None, worker: Any = None,
        cancelled: postprocess_worker.Cancellation | None = None) -> tuple[str, dict[str, Any]]:
    job = repositories.get("jobs", job_id, include_private=True)
    if job is None:
        raise KeyError(job_id)
    turn = repositories.get("turns", job["turn_id"])
    if turn is None or turn["operation"] not in LOCAL_OPERATIONS:
        raise ValueError("job is not a local processing task")
    if job["state"] != "queued":
        return str(job["state"]), {"job": repositories.get("jobs", job_id)}
    parameters = turn["parameters"]
    source = parameters["source"]
    last_heartbeat = time.monotonic()
    last_saved = 0.0
    last_stage: tuple[Any, ...] = ()

    class Cancellation:
        def is_set(self) -> bool:
            nonlocal last_heartbeat
            if worker is not None and time.monotonic() - last_heartbeat >= worker.heartbeat_seconds:
                worker.heartbeat()
                last_heartbeat = time.monotonic()
            current = repositories.get("jobs", job_id, include_private=True)
            return (current is None or bool(current.get("cancel_intent"))
                    or (cancelled is not None and cancelled.is_set())
                    or current["state"] not in {"queued", "running"}
                    or (worker is not None and not worker.owns(job_id)))

    cancellation = Cancellation()

    def update(value: dict[str, Any]) -> None:
        nonlocal last_saved, last_stage
        value = {**value, "job_id": job_id}
        stage = (value.get("stage"), value.get("unit"), value.get("operation"))
        now = time.monotonic()
        if (stage != last_stage or now - last_saved >= 0.2
                or value.get("completed") == value.get("total")):
            with repositories.transaction():
                if cancellation.is_set():
                    raise ValueError("postprocess cancelled")
                repositories.update("jobs", job_id, {"progress": value, "updated_at": _now()})
                repositories.append_event(job_id, "progress", value)
            last_stage, last_saved = stage, now
        if progress is not None:
            progress(value)

    def finish(state: str, *, artifact: dict[str, Any] | None = None,
               error: BaseException | None = None,
               saving: bool = False) -> tuple[str, dict[str, Any]]:
        changes: dict[str, Any] = {"state": state, "updated_at": _now()}
        if artifact is not None:
            changes.update({"actual_count": 1, "local_save_state": "saved",
                            "progress": {"stage": "completed", "completed": 1,
                                         "total": 1, "unit": "artifacts",
                                         "operation": turn["operation"]}})
        if error is not None:
            changes["error"] = {"type": "save_failed" if saving else "validation_error",
                                "detail": postprocess_worker._error_message(error)}
            changes["local_save_state"] = "failed" if saving else "not_started"
        with repositories.transaction():
            if worker is not None and not worker.owns(job_id):
                current = repositories.get("jobs", job_id)
                return "conflict", {"job": current, "source": source}
            repositories.update("jobs", job_id, changes)
            repositories.update("turns", turn["id"],
                                {"creation_state": state, "finished_at": _now()})
            repositories.append_event(job_id, state, {})
        result = {"job": repositories.get("jobs", job_id), "source": source}
        if artifact is not None:
            result.update({"artifact": artifact, "artifacts": [artifact]})
            report(progress, "completed", completed=1, total=1, unit="artifacts")
        return ("save_failed" if saving else state), result

    saving = False
    try:
        with repositories.transaction():
            if cancellation.is_set():
                return finish("canceled")
            changes: dict[str, Any] = {"state": "running", "updated_at": _now()}
            if worker is None:
                from imagen_cli.application.cancel_service import executor_id

                changes["executor_id"] = executor_id()
            repositories.update("jobs", job_id, changes)
            repositories.update("turns", turn["id"], {"creation_state": "running"})
        path = store.get(source)
        if (path is None
                or hashlib.sha256(path.read_bytes()).hexdigest() != parameters["source_sha256"]):
            raise ValueError("source artifact bytes changed or are unavailable")
        session = repositories.get("sessions", turn["session_id"])
        if session is None or session.get("deleted_at"):
            raise ValueError("destination session is unavailable")
        data, metadata = postprocess_worker.execute(
            path, parameters["pipeline"], timeout=parameters["timeout"],
            budget_approval=parameters.get("budget_approval"), cancelled=cancellation,
            progress=update, source_sha256=parameters["source_sha256"],
        )
        update({"stage": "saving", "completed": 0, "total": None, "unit": None,
                "operation": turn["operation"]})
        saving = True
        artifact = postprocess_service.publish_result(
            repositories, store, source, parameters["pipeline"], data, metadata,
            session_id=turn["session_id"], job_id=job_id, defer_publish=True,
        )
        with repositories.transaction():
            if cancellation.is_set():
                return finish("canceled")
            session = repositories.get("sessions", turn["session_id"])
            if session is None or session.get("deleted_at"):
                raise ValueError("destination session is unavailable")
            store.publish(artifact["id"])
            if cancellation.is_set():
                raise ValueError("postprocess cancelled")
            if worker is not None and not worker.owns(job_id):
                from imagen_cli.execution.worker import LeaseError

                raise LeaseError("local publication lease expired")
            return finish("succeeded", artifact=dict(repositories.get("artifacts", artifact["id"])))
    except KeyboardInterrupt:
        return finish("canceled")
    except (ValueError, KeyError, OSError, RuntimeError) as error:
        if cancellation.is_set():
            return finish("canceled")
        return finish("failed", error=error, saving=saving)
