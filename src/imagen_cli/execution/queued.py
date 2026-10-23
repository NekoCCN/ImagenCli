"""Run one queued async job exactly once, using its frozen turn snapshot."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from imagen_cli.application.artifact_service import ArtifactService
from imagen_cli.application.outcome_service import OutcomeService
from imagen_cli.application.profile_service import ProfileService
from imagen_cli.execution.foreground import Adapter, ForegroundExecutor
from imagen_cli.providers.base import ValidatedRequest
from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.private_state import PrivateStateStore
from imagen_cli.storage.repositories import Repositories


def build_request(
    repositories: Repositories, store: ArtifactStore, job: dict[str, Any],
) -> ValidatedRequest:
    turn = repositories.get("turns", job["turn_id"])
    if turn is None or turn.get("job_id") != job["id"]:
        raise ValueError("job must belong to its producer turn")
    snapshot = turn.get("resolved_profile_snapshot") or {}
    identity = snapshot.get("profile_identity")
    if not isinstance(identity, str) or not identity:
        raise ValueError("profile identity is unavailable")
    profile_row = repositories.get("profiles", identity, include_private=True)
    if profile_row is None:
        raise ValueError("profile is unavailable")
    profile = ProfileService().load(profile_row)
    targets = profile.trust_policy.get("allowed_upload_targets", [profile.base_url])
    recorded = snapshot.get("allowed_upload_targets")
    if not isinstance(targets, list) or not isinstance(recorded, list):
        raise ValueError("upload targets are invalid")
    if (
        profile.id != identity
        or profile.revision != snapshot.get("profile_revision")
        or profile.provider != snapshot.get("provider")
        or profile.family != snapshot.get("family")
        or profile.base_url != snapshot.get("endpoint_origin")
        or list(targets) != list(recorded)
    ):
        raise ValueError("resolved profile drifted")
    contents: list[tuple[int, bytes]] = []
    mask: bytes | None = None
    for item in turn.get("input_manifest") or []:
        identity = item.get("artifact_id")
        path = store.get(identity)
        row = repositories.get("artifacts", identity)
        if path is None or row is None:
            raise ValueError("input artifact is unavailable")
        content = path.read_bytes()
        if row.get("sha256") != hashlib.sha256(content).hexdigest():
            raise ValueError("input artifact bytes changed")
        if item.get("role") == "mask":
            mask = content
        else:
            contents.append((int(item.get("index", 0)), content))
    contents.sort(key=lambda pair: pair[0])
    digest = hashlib.sha256(json.dumps(
        {
            "target": snapshot, "model": turn["model"],
            "operation": turn["operation"], "parameters": turn["parameters"],
        },
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    return ValidatedRequest(
        plan_id=job.get("plan_id") or "async",
        job_id=job["id"],
        profile_identity=profile.id,
        profile_revision=profile.revision,
        operation=turn["operation"],
        model=turn["model"],
        model_revision=str(snapshot.get("model_revision") or "local"),
        prompt=turn["instruction"],
        normalized_parameters=turn["parameters"],
        capability_digest=digest,
        authorized=True,
        dispatch_intent_recorded=False,
        inputs=tuple(content for _, content in contents),
        mask=mask,
    )


def run_queued(
    repositories: Repositories,
    store: ArtifactStore,
    private_store: PrivateStateStore,
    adapter: Adapter,
    job_id: str,
) -> dict[str, Any]:
    job = repositories.get("jobs", job_id, include_private=True)
    if job is None:
        raise KeyError(job_id)
    if job.get("state") != "queued" or job.get("dispatch_state") != "not_started":
        raise ValueError("job is not queued and undispatched")
    outcomes = OutcomeService(repositories)
    try:
        request = build_request(repositories, store, job)
    except (KeyError, OSError, ValueError):
        return outcomes.fail(job_id, error_type="validation_error", accepted_maybe=False)
    executor = ForegroundExecutor(
        repositories, adapter, ArtifactService(repositories, store), outcomes, private_store,
    )
    return executor.execute(request, requested_count=job.get("requested_count"))
