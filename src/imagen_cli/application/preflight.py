"""Offline preflight and immutable creation planning; never dispatch here."""

import hashlib
import json
from pathlib import Path
from typing import Any

from imagen_cli.application.planning_service import PlanningService
from imagen_cli.application.profile_service import ProfileService
from imagen_cli.application.session_service import SessionService
from imagen_cli.domain.contracts import CreationInput
from imagen_cli.domain.primitive import new_id, sha256_bytes
from imagen_cli.i18n import t
from imagen_cli.providers.routes import SUPPORTED_ROUTES
from imagen_cli.providers.vocabulary import check
from imagen_cli.storage.artifacts import MAX_BYTES, ArtifactStore, inspect_media
from imagen_cli.storage.repositories import Repositories


def plan_creation(
    repositories: Repositories, artifacts: ArtifactStore, creation: CreationInput,
    *, dry_run: bool = False, asynchronous: bool = False,
) -> tuple[str, dict[str, Any]]:
    if asynchronous:
        from imagen_cli.execution import worker

        if not worker.detached_supported():
            return "unsupported_capability", {}
    sessions = SessionService(repositories, artifacts)
    from imagen_cli.cli.commands.session import show

    status, snapshot = show(sessions, creation.session)
    if status != "succeeded":
        return status, {}
    session_id = snapshot["session"]["id"]
    session = sessions.resume(session_id)["session"]
    identity = creation.profile or session.default_profile_id
    row = repositories.get("profiles", identity) if identity else None
    if row is None:
        return "not_found", {}
    profile = ProfileService().load(row)
    model = creation.model or session.default_model or profile.model_default
    model_revision = "local"
    if (profile.provider, profile.family) not in SUPPORTED_ROUTES:
        return "unsupported_capability", {
            "error": {
                "type": "unsupported_capability",
                "detail": t(
                    f"this build cannot dispatch {profile.provider} {profile.family}; "
                    "supported routes are gemini generate_content and openai images",
                    f"当前版本无法派发 {profile.provider} {profile.family}；"
                    "可派发的路线是 gemini generate_content 与 openai images",
                ),
            }
        }
    parameters = dict(creation.provider_params)
    if "count" in parameters or "size" in parameters:
        return "validation_error", {}
    parameters["count"] = creation.count
    for name in ("size", "aspect_ratio", "image_size"):
        value = getattr(creation, name)
        if value is not None:
            parameters[name] = value
    reason = check(profile.provider, profile.family, model, creation.operation, parameters)
    if reason is not None:
        # Refuse here, not at dispatch: an approved plan must be sendable as approved.
        return "validation_error", {"error": {"type": "validation_error", "detail": reason}}
    if creation.mask or creation.references or creation.retry_of_job_id:
        return "unsupported_capability", {}
    input_hashes: list[str] = []
    input_artifact_ids: list[str] = []
    total_bytes = 0
    pending_inputs: list[tuple[str, bytes]] = []
    for source in creation.inputs:
        try:
            if source.startswith("artifact:"):
                identity = source.removeprefix("artifact:")
                path = artifacts.get(identity)
                if path is None:
                    return "not_found", {}
            else:
                identity = new_id("art")
                path = Path(source.removeprefix("file:"))
            if not path.is_file():
                return "validation_error", {}
            with path.open("rb") as stream:
                content = stream.read(MAX_BYTES + 1)
            total_bytes += len(content)
            if not content or len(content) > MAX_BYTES or total_bytes > 256 * 1024 * 1024:
                return "validation_error", {}
            inspect_media(content)
            if source.startswith("file:"):
                pending_inputs.append((identity, content))
            input_hashes.append(sha256_bytes(content))
            input_artifact_ids.append(identity)
        except (OSError, ValueError):
            return "validation_error", {}
    for identity, content in pending_inputs:
        artifacts.stage(identity, content, origin_manifest={"kind": "input"})
        artifacts.publish(identity)
    context = (
        {"kind": "explicit_images", "input_artifact_ids": input_artifact_ids,
         "mask_artifact_id": None}
        if input_artifact_ids else {"kind": "none"}
    )
    target: dict[str, Any] = {
        "profile_identity": profile.id, "profile_revision": profile.revision,
        "endpoint_origin": profile.base_url,
        "allowed_upload_targets": profile.trust_policy.get(
            "allowed_upload_targets", [profile.base_url],
        ),
        "provider": profile.provider, "family": profile.family,
        "model_revision": model_revision,
    }
    if asynchronous and not dry_run:
        # Durable queue entry only: no approval is consumed and nothing is sent.
        turn = sessions.create_turn(
            session.id, instruction=creation.prompt, operation=creation.operation,
            resolved_profile_snapshot=target, model=model, parameters=parameters,
            requested_count=creation.count, parent_turn_id=session.current_turn_id,
            references={"input": input_artifact_ids} if input_artifact_ids else None,
        )
        repositories.update("jobs", turn.job_id, {"worker_queued": True})
        return "accepted", {
            "job": repositories.get("jobs", turn.job_id),
            "turn": repositories.get("turns", turn.id),
        }
    plan, _ = PlanningService(repositories).create_plan(
        session_id=session.id, target_snapshot=target, operation=creation.operation,
        model=model, normalized_parameters=parameters, prompt_snapshot=creation.prompt,
        input_hashes=input_hashes, portable_context=context,
        capability_digest=_target_digest(target, model, creation.operation, parameters),
    )
    return "planned" if dry_run else "approval_required", {"plan": plan.model_dump(mode="json")}


def _target_digest(
    target: dict[str, Any], model: str, operation: str, parameters: dict[str, Any],
) -> str:
    payload = json.dumps(
        {"target": target, "model": model, "operation": operation, "parameters": parameters},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
