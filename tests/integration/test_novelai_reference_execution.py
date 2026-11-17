import hashlib
import json

import pytest

from imagen_cli.application.container import Container
from imagen_cli.application.planning_service import PlanningService
from imagen_cli.domain.contracts import CreationInput
from imagen_cli.domain.primitive import new_id
from imagen_cli.execution.queued import build_request
from imagen_cli.providers.base import ReferenceInput
from imagen_cli.providers.novelai_references import reference_transformation
from tests.integration.test_approved_execution import service, setup


def test_reference_options_require_explicit_aligned_roles():
    base = dict(
        schema_version=1,
        operation="generate",
        session="test",
        prompt="test",
        references=["file:image.png", "file:image.png"],
    )
    with pytest.raises(ValueError):
        CreationInput(**base)
    creation = CreationInput(
        **base,
        reference_options=[
            {"kind": "vibe", "settings": {"strength": 0.3}},
            {"kind": "precise", "settings": {}},
        ],
    )
    assert [item.kind for item in creation.reference_options] == ["vibe", "precise"]


def reference_plan(container, png_bytes):
    old, _, validator, executor, adapter, _ = setup(container, png_bytes)
    identity = new_id("art")
    container.artifact_store.stage(identity, png_bytes)
    container.artifact_store.publish(identity)
    target = dict(old.target_snapshot)
    container.repositories.update(
        "profiles",
        "offline",
        {
            "provider": "novelai",
            "family": "generate_image",
            "novelai_options": {"auth_mode": "bearer", "reference_transport": "raw"},
        },
    )
    target.update(
        provider="novelai",
        family="generate_image",
        novelai_options={"auth_mode": "bearer", "reference_transport": "raw"},
    )
    options = (
        {"kind": "vibe", "settings": {"strength": 0.3}},
        {"kind": "precise", "settings": {"strength": 0.7}},
    )
    context = {
        "kind": "explicit_images",
        "input_artifact_ids": [identity],
        "mask_artifact_id": None,
        "references": [{"artifact_id": identity, **item} for item in options],
    }
    for entry in context["references"]:
        entry["sha256"] = hashlib.sha256(png_bytes).hexdigest()
    context["references"][1]["transformation"] = reference_transformation(
        ReferenceInput(png_bytes, "precise", options[1]["settings"])
    )
    plan, _ = PlanningService(container.repositories).create_plan(
        session_id=old.session_id,
        target_snapshot=target,
        operation="generate",
        model="x",
        normalized_parameters={"count": 1},
        prompt_snapshot="test",
        input_hashes=[hashlib.sha256(png_bytes).hexdigest()] * 3,
        portable_context=context,
        capability_digest=old.capability_digest,
    )
    PlanningService(container.repositories).approve(plan.id, plan.plan_digest)
    return plan, service(container, validator, executor), adapter, identity, options


def dispatch(api, plan, identity, options):
    return api.execute_approved(
        plan.id,
        plan.plan_digest,
        model_revision="2026-01",
        input_artifact_ids=(identity,),
        reference_artifact_ids=(identity, identity),
        reference_options=options,
    )


def test_foreground_and_queue_keep_repeated_image_roles(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        plan, api, adapter, identity, options = reference_plan(container, png_bytes)
        result = dispatch(api, plan, identity, options)
        request = adapter.requests[0]
        assert request.inputs == (png_bytes,)
        assert [item.kind for item in request.references] == ["vibe", "precise"]
        assert [item.settings["strength"] for item in request.references] == [0.3, 0.7]
        recovered = build_request(container.repositories, container.artifact_store, result["job"])
        assert recovered.inputs == request.inputs
        assert recovered.references == request.references
        assert len(result["turn"]["input_manifest"]) == 3


@pytest.mark.parametrize("drift", ["settings", "auth", "transport", "endpoint", "image"])
def test_foreground_approval_detects_reference_and_profile_drift(tmp_path, png_bytes, drift):
    with Container(workspace=str(tmp_path)) as container:
        plan, api, adapter, identity, options = reference_plan(container, png_bytes)
        if drift == "settings":
            options = ({"kind": "vibe", "settings": {"strength": 0.4}}, options[1])
        elif drift in {"auth", "transport"}:
            container.repositories.update(
                "profiles",
                "offline",
                {
                    "novelai_options": {
                        "auth_mode": "x-api-key" if drift == "auth" else "bearer",
                        "reference_transport": "encoded" if drift == "transport" else "raw",
                    }
                },
            )
        elif drift == "endpoint":
            container.repositories.update("profiles", "offline", {"base_url": "https://other.test"})
        else:
            container.artifact_store.get(identity).write_bytes(b"changed")
        with pytest.raises(ValueError):
            dispatch(api, plan, identity, options)
        assert not adapter.requests


@pytest.mark.parametrize(
    "drift", ["settings", "role", "auth", "transport", "endpoint", "image", "parameters"]
)
def test_queue_recovery_detects_approval_drift(tmp_path, png_bytes, drift):
    with Container(workspace=str(tmp_path)) as container:
        plan, api, adapter, identity, options = reference_plan(container, png_bytes)
        result = dispatch(api, plan, identity, options)
        repo = container.repositories
        turn = result["turn"]
        if drift in {"settings", "role"}:
            manifest = turn["input_manifest"]
            if drift == "settings":
                manifest[1]["settings"]["strength"] = 0.4
            else:
                manifest[0]["role"] = "mask"
            container.connection.execute(
                "UPDATE turns SET payload=json_set(payload,'$.input_manifest',json(?)) WHERE id=?",
                (json.dumps(manifest), turn["id"]),
            )
        elif drift in {"auth", "transport"}:
            repo.update(
                "profiles",
                "offline",
                {
                    "novelai_options": {
                        "auth_mode": "x-api-key" if drift == "auth" else "bearer",
                        "reference_transport": "encoded" if drift == "transport" else "raw",
                    }
                },
            )
        elif drift == "endpoint":
            repo.update("profiles", "offline", {"base_url": "https://other.test"})
        elif drift == "parameters":
            container.connection.execute(
                "UPDATE turns SET payload=json_set(payload,'$.parameters',json(?)) WHERE id=?",
                (json.dumps({"count": 2}), turn["id"]),
            )
        else:
            container.artifact_store.get(identity).write_bytes(b"changed")
        with pytest.raises(ValueError):
            build_request(repo, container.artifact_store, result["job"])


def test_preflight_binds_explicit_reference_roles_without_primary_images(
    tmp_path, png_bytes, monkeypatch
):
    from imagen_cli.application import preflight

    with Container(workspace=str(tmp_path)) as container:
        old, _, _, identity, _ = reference_plan(container, png_bytes)
        monkeypatch.setattr(preflight, "SUPPORTED_ROUTES", {("novelai", "generate_image")})
        creation = CreationInput.model_validate(
            {
                "schema_version": 1,
                "operation": "generate",
                "session": "work",
                "prompt": "test",
                "profile": "offline",
                "model": "nai-diffusion-4-5-full",
                "references": [f"artifact:{identity}", f"artifact:{identity}"],
                "reference_options": [
                    {"kind": "precise", "settings": {"strength": 0.2}},
                    {"kind": "precise", "settings": {"strength": 0.8}},
                ],
            }
        )
        status, result = preflight.plan_creation(
            container.repositories, container.artifact_store, creation, dry_run=True
        )
        assert status == "planned"
        context = result["plan"]["portable_context"]
        assert context["kind"] == "none"
        assert [item["settings"]["strength"] for item in context["references"]] == [0.2, 0.8]
        assert context["references"][0]["transformation"]["algorithm"] == "fit-pad-black-png-v1"
        assert len(result["plan"]["input_hashes"]) == 2
        assert result["plan"]["target_snapshot"]["novelai_options"]["reference_transport"] == "raw"


@pytest.mark.parametrize("drift", ["output", "version"])
def test_queue_refuses_changed_preprocessing_output(tmp_path, png_bytes, monkeypatch, drift):
    from imagen_cli.providers import novelai_references

    with Container(workspace=str(tmp_path)) as container:
        plan, api, _, identity, options = reference_plan(container, png_bytes)
        result = dispatch(api, plan, identity, options)
        if drift == "output":
            monkeypatch.setattr(
                novelai_references, "prepare_precise_reference", lambda _: png_bytes
            )
        else:
            monkeypatch.setattr(novelai_references, "PRECISE_ALGORITHM", "changed-v2")
        with pytest.raises(ValueError, match="transformation"):
            build_request(container.repositories, container.artifact_store, result["job"])
