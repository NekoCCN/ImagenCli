"""Recover provider outputs from durable local responses; never create remote work."""

import base64
from typing import Any

from imagen_cli.application.artifact_service import ArtifactService
from imagen_cli.application.outcome_service import OutcomeService
from imagen_cli.storage.artifacts import MAX_BYTES, inspect_media
from imagen_cli.storage.download import fetch
from imagen_cli.storage.private_state import PrivateStateStore


def _missing_outputs(
    service: ArtifactService, private: PrivateStateStore, job_id: str,
) -> list[tuple[int, Any]] | None:
    repo = service.repositories
    with repo.transaction():
        job = repo.get("jobs", job_id, include_private=True)
        if (
            not job or job.get("remote_state") != "succeeded"
            or job.get("dispatch_state") != "response_received"
            or not job.get("private_locator_ref")
        ):
            return None
        turn = repo.get("turns", job["turn_id"])
        if not turn or turn.get("job_id") != job_id:
            raise ValueError("job must belong to its producer turn")
        snapshot = turn.get("resolved_profile_snapshot", {})
        if (snapshot.get("provider"), snapshot.get("family")) != ("openai", "images"):
            return None
        rows = repo.list("artifacts", producer_job_id=job_id)
        occupied = {row["origin_manifest"].get("output_index") for row in rows}
        count = job.get("actual_count", 0)
        if type(count) is not int or not 1 <= count <= 10:
            return None
        missing = [index for index in range(count) if index not in occupied]
        if not missing:
            return []
        body = private.read_response(job_id, job["private_locator_ref"])
        outputs = body.get("data")
        if not isinstance(outputs, list) or len(outputs) != count:
            raise ValueError("response output count does not match durable job")
        return [(index, outputs[index]) for index in missing]


def _content(output: Any) -> bytes:
    encoded = output.get("b64_json") if isinstance(output, dict) else None
    if isinstance(encoded, str) and encoded:
        if len(encoded) > 4 * ((MAX_BYTES + 2) // 3):
            raise ValueError("invalid inline output")
        content = base64.b64decode(encoded, validate=True)
    else:
        url = output.get("url") if isinstance(output, dict) else None
        if not isinstance(url, str) or not url:
            raise ValueError("invalid output")
        content = fetch(url, max_bytes=MAX_BYTES)
    inspect_media(content)
    return content


def recover_inline(service: ArtifactService, private: PrivateStateStore, job_id: str) -> None:
    repo = service.repositories
    missing = _missing_outputs(service, private, job_id)
    if missing is None:
        return
    if missing:
        # Downloads happen outside any write transaction.
        decoded = [(index, _content(output)) for index, output in missing]
        for index, content in decoded:
            try:
                service.save(job_id, content, index=index)
            except (OSError, ValueError):
                return
    complete = repo.list("artifacts", producer_job_id=job_id)
    for row in complete:
        try:
            service.reconcile(row["id"])
            if service.artifact_store.get(row["id"]) is None:
                return
        except (OSError, ValueError):
            return
    job = repo.get("jobs", job_id)
    if job and len(complete) == job.get("actual_count"):
        OutcomeService(repo).complete(job_id, actual_count=job["actual_count"])
