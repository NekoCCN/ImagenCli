"""Run one queued async job exactly once, using its frozen turn snapshot."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from imagen_cli.application.artifact_service import ArtifactService
from imagen_cli.application.outcome_service import OutcomeService
from imagen_cli.application.profile_service import ProfileService
from imagen_cli.domain.contracts import ReferenceOptions
from imagen_cli.execution.foreground import Adapter, ForegroundExecutor
from imagen_cli.providers.base import ReferenceInput, ValidatedRequest
from imagen_cli.storage.artifacts import MAX_BYTES, ArtifactStore
from imagen_cli.storage.private_state import PrivateStateStore
from imagen_cli.storage.repositories import Repositories


def build_request(
    repositories: Repositories,
    store: ArtifactStore,
    job: dict[str, Any],
    *,
    private_store: PrivateStateStore | None = None,
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
    if profile.provider == "novelai":
        options = (profile.novelai_options.model_dump(mode="json") if profile.novelai_options
                   else {"auth_mode": "bearer", "reference_transport": "encoded"})
        if options != snapshot.get("novelai_options"):
            raise ValueError("reference or authentication configuration drifted")
    plan_id = job.get("plan_id")
    if plan_id:
        from imagen_cli.application.planning_service import PlanningService

        plan = repositories.get("plans", plan_id)
        if plan is None:
            raise ValueError("approved plan unavailable")
        planning = PlanningService(repositories)
        plan = planning._validated_plan(plan_id, plan["plan_digest"])
        approval = planning._approval(plan_id)
        if (approval.get("state") != "consumed"
                or approval.get("execution_job_id") != job["id"]
                or approval.get("plan_digest") != plan["plan_digest"]
                or plan["session_id"] != turn["session_id"]):
            raise ValueError("queued job is not bound to its consumed approval")
        if (turn["parameters"] != plan["normalized_parameters"]
                or snapshot != plan["target_snapshot"]
                or turn["instruction"] != plan["prompt_snapshot"]
                or turn["model"] != plan["model"]
                or turn["operation"] != plan["operation"]):
            raise ValueError("approved request drifted")
        manifest = turn.get("input_manifest") or []
        if [item.get("sha256") for item in manifest] != plan["input_hashes"]:
            raise ValueError("approved input manifest drifted")
        refs = [
            {
                "artifact_id": item["artifact_id"],
                "kind": item.get("kind"),
                "settings": item.get("settings", {}),
                "sha256": item.get("sha256"),
                **({"transformation": item["transformation"]} if "transformation" in item else {}),
                **({"encoding": item["encoding"]} if "encoding" in item else {}),
            }
            for item in manifest
            if item.get("role") == "reference"
        ]
        context = plan["portable_context"]
        expected = [("input", index, identity)
                    for index, identity in enumerate(context.get("input_artifact_ids", []))]
        if context.get("mask_artifact_id") is not None:
            expected.append(("mask", 0, context["mask_artifact_id"]))
        expected.extend(("reference", index, item["artifact_id"])
                        for index, item in enumerate(context.get("references", [])))
        if [(item.get("role"), item.get("index"), item.get("artifact_id"))
                for item in manifest] != expected:
            raise ValueError("approved input roles drifted")
        if refs != context.get("references", []):
            raise ValueError("approved reference settings drifted")
    contents: list[tuple[int, bytes]] = []
    references: list[tuple[int, ReferenceInput]] = []
    mask: bytes | None = None
    total_bytes = 0
    for item in turn.get("input_manifest") or []:
        identity = item.get("artifact_id")
        path = store.get(identity)
        row = repositories.get("artifacts", identity)
        if path is None or row is None:
            raise ValueError("input artifact is unavailable")
        with path.open("rb") as stream:
            content = stream.read(MAX_BYTES + 1)
        total_bytes += len(content)
        if not content or len(content) > MAX_BYTES or total_bytes > 256 * 1024 * 1024:
            raise ValueError("queued input byte limit exceeded")
        measured_hash = hashlib.sha256(content).hexdigest()
        if row.get("sha256") != measured_hash or (
            item.get("sha256") is not None and item["sha256"] != measured_hash
        ):
            raise ValueError("input artifact bytes changed")
        if item.get("role") == "mask":
            mask = content
        elif item.get("role") == "reference":
            option = ReferenceOptions.model_validate({
                "kind": item.get("kind"), "settings": item.get("settings", {}),
            })
            reference = ReferenceInput(content, option.kind, option.settings)
            if option.kind == "precise":
                from imagen_cli.providers.novelai_references import bind_precise_reference

                reference = bind_precise_reference(
                    reference,
                    expected=item.get("transformation", {}),
                )
            if option.kind == "vibe" and (
                profile.novelai_options is None
                or profile.novelai_options.reference_transport == "encoded"
            ):
                from imagen_cli.application.novelai_vibe_service import VibePreparationService

                if private_store is None:
                    raise ValueError("private Vibe cache unavailable")
                reference = VibePreparationService(repositories, store, private_store).bind(
                    profile,
                    reference,
                    turn["model"],
                    expected=item.get("encoding", {}),
                )
            references.append((int(item.get("index", 0)), reference))
        elif item.get("role") == "input":
            contents.append((int(item.get("index", 0)), content))
        else:
            raise ValueError("unsupported input role")
    contents.sort(key=lambda pair: pair[0])
    references.sort(key=lambda pair: pair[0])
    digest = hashlib.sha256(json.dumps(
        {
            "target": snapshot, "model": turn["model"],
            "operation": turn["operation"], "parameters": turn["parameters"],
            "input_manifest": turn.get("input_manifest") or [],
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
        references=tuple(reference for _, reference in references),
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
        request = build_request(repositories, store, job, private_store=private_store)
    except (KeyError, OSError, ValueError) as error:
        return outcomes.fail(
            job_id, error_type="validation_error", accepted_maybe=False,
            error_class=type(error).__name__,
        )
    executor = ForegroundExecutor(
        repositories, adapter, ArtifactService(repositories, store), outcomes, private_store,
    )
    return executor.execute(request, requested_count=job.get("requested_count"))
