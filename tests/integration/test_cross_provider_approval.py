"""T052: cross-provider approval discloses exact prompt/assets and transfers no native state."""

import hashlib
from typing import Any

import pytest

from imagen_cli.application.artifact_service import ArtifactService
from imagen_cli.application.container import Container
from imagen_cli.application.execution_service import ExecutionService
from imagen_cli.application.outcome_service import OutcomeService
from imagen_cli.application.planning_service import PlanningService
from imagen_cli.application.session_service import SessionService
from imagen_cli.domain.primitive import new_id
from imagen_cli.execution.foreground import ForegroundExecutor
from imagen_cli.providers.base import ArtifactDescriptor, ImmediateResult
from tests.support import capability


class RecordingAdapter:
    def __init__(self, png: bytes) -> None:
        self.png = png
        self.requests: list[Any] = []
        self.uploads = 0

    def create(self, request: Any) -> ImmediateResult:
        self.requests.append(request)
        self.uploads += len(request.inputs) + (1 if request.mask is not None else 0)
        return ImmediateResult(
            1, 1, (ArtifactDescriptor(new_id("art"), 0, self.png),),
        )


def _profile(
    identity: str, provider: str, family: str, base_url: str, model: str,
) -> dict[str, Any]:
    return {
        "id": identity, "name": identity, "provider": provider, "family": family,
        "base_url": base_url, "revision": 1, "model_default": model,
        "credential_ref": "fixture-not-resolved",
        "trust_policy": {"allowed_upload_targets": [base_url]},
    }


def _setup(container: Container, png: bytes, *, approved: bool):
    repository = container.repositories
    session = SessionService(repository, container.artifact_store).create_session("cross")
    source = _profile(
        "openai-source", "openai", "images", "https://api.openai.com/v1", "gpt-image-2.5-sunburst",
    )
    target = _profile(
        "gemini-target", "gemini", "generate_content",
        "https://generativelanguage.googleapis.com/v1beta", "gemini-2.5-flash-image",
    )
    repository.insert("profiles", source)
    repository.insert("profiles", target)
    record = capability(
        id="gemini-cross-edit", provider="gemini", family="generate_content",
        model="gemini-2.5-flash-image", model_revision="2026-10", operation="edit",
        parameter_schema={
            "type": "object", "properties": {"count": {"const": 1}},
            "additionalProperties": False,
        },
        continuation_strategy="explicit_images", transport_kind="sync",
    )
    identity = new_id("art")
    container.artifact_store.stage(identity, png)
    container.artifact_store.publish(identity)
    digest = hashlib.sha256(png).hexdigest()
    plan, approval = PlanningService(repository).create_plan(
        session_id=session.id,
        target_snapshot={
            "profile_identity": target["id"], "profile_revision": 1,
            "endpoint_origin": target["base_url"],
            "allowed_upload_targets": [target["base_url"]],
            "provider": target["provider"], "family": target["family"],
            "model_revision": "2026-10",
        },
        operation="edit", model="gemini-2.5-flash-image",
        normalized_parameters={"count": 1}, prompt_snapshot="exact cross-provider prompt: 红色风筝",
        input_hashes=[digest],
        portable_context={
            "kind": "explicit_images", "input_artifact_ids": [identity],
            "mask_artifact_id": None,
        },
        capability_digest=record["digest"],
        context_loss=["source native context is not portable across providers"],
    )
    if approved:
        PlanningService(repository).approve(plan.id, plan.plan_digest)
    adapter = RecordingAdapter(png)
    executor = ForegroundExecutor(
        repository, adapter, ArtifactService(repository, container.artifact_store),
        OutcomeService(repository), container.private_state_store,
    )
    api = ExecutionService(repository, container.artifact_store, executor)
    return plan, approval, api, adapter, identity, digest, source, target


def test_unapproved_cross_provider_plan_uploads_nothing(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        plan, _, api, adapter, identity, _, _, _ = _setup(
            container, png_bytes, approved=False,
        )
        with pytest.raises(ValueError):
            api.execute_approved(
                plan.id, plan.plan_digest, model_revision="2026-10",
                input_artifact_ids=(identity,),
            )
        assert adapter.uploads == 0
        assert adapter.requests == []
        assert container.repositories.list("jobs") == []
        assert container.repositories.list("turns") == []


def test_approved_cross_provider_plan_transfers_exact_text_and_assets_only(
    tmp_path, png_bytes,
):
    with Container(workspace=str(tmp_path)) as container:
        plan, _, api, adapter, identity, digest, source, target = _setup(
            container, png_bytes, approved=True,
        )
        # Disclosure is exact before any request.
        assert plan.prompt_snapshot == "exact cross-provider prompt: 红色风筝"
        assert plan.input_hashes == [digest]
        assert plan.portable_context == {
            "kind": "explicit_images", "input_artifact_ids": [identity],
            "mask_artifact_id": None,
        }
        assert plan.context_loss == [
            "source native context is not portable across providers"
        ]
        assert plan.target_snapshot["profile_identity"] == target["id"]
        assert plan.target_snapshot["provider"] == "gemini"
        assert plan.target_snapshot["allowed_upload_targets"] == [target["base_url"]]
        assert "native" not in {key.casefold() for key in plan.portable_context}

        result = api.execute_approved(
            plan.id, plan.plan_digest, model_revision="2026-10",
            input_artifact_ids=(identity,),
        )
        assert result["status"] == "succeeded"
        assert len(adapter.requests) == 1
        transferred = adapter.requests[0]
        assert transferred.prompt == plan.prompt_snapshot
        assert transferred.inputs == (png_bytes,)
        assert transferred.mask is None
        assert transferred.native_context is None
        assert transferred.profile_identity == target["id"]
        assert transferred.profile_identity != source["id"]
        assert transferred.profile_revision == 1
        assert not hasattr(transferred, "previous_response_id")
        assert adapter.uploads == 1
        # The source profile is never selected by the plan or execution.
        assert container.repositories.get("profiles", source["id"])["provider"] == "openai"


def test_target_provider_drift_after_approval_blocks_the_cross_provider_transfer(
    tmp_path, png_bytes,
):
    with Container(workspace=str(tmp_path)) as container:
        plan, _, api, adapter, identity, _, _, _ = _setup(
            container, png_bytes, approved=True,
        )
        container.repositories.update(
            "profiles", "gemini-target", {"provider": "openai", "revision": 2},
        )
        with pytest.raises(ValueError):
            api.execute_approved(
                plan.id, plan.plan_digest, model_revision="2026-10",
                input_artifact_ids=(identity,),
            )
        assert adapter.uploads == 0
        assert adapter.requests == []
        assert container.repositories.list("jobs") == []
