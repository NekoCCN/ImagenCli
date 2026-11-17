"""Real planning/execution/recovery with only the provider transport mocked."""

import base64
import json

import httpx
import pytest

from imagen_cli.application.container import Container
from imagen_cli.application.planning_service import PlanningService
from imagen_cli.application.preflight import plan_creation
from imagen_cli.domain.contracts import CreationInput
from imagen_cli.execution.queued import build_request
from imagen_cli.providers.novelai import NovelAIAdapter
from tests.integration.test_approved_execution import service, setup
from tests.unit.test_novelai_vibe_cache import setup_vibe


@pytest.mark.parametrize("workflow", ["vibe", "precise", "img2img"])
def test_approved_workflow_preserves_roles_seed_count_and_recovery(tmp_path, png_bytes, workflow):
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200, json={"images": [{"image": base64.b64encode(png_bytes).decode(), "seed": 123}]}
        )

    with Container(workspace=str(tmp_path)) as container:
        _, _, validator, executor, _, _ = setup(container, png_bytes)
        preparation, profile, identity, encoder = setup_vibe(container, png_bytes)
        if workflow == "vibe":
            planned = preparation.plan(profile.id, identity, profile.model_default, 0.6)
            assert not encoder.calls
            preparation.execute(planned["id"], planned["digest"])
            reused = preparation.plan(profile.id, identity, profile.model_default, 0.6)
            preparation.execute(reused["id"], reused["digest"])
            assert len(encoder.calls) == 1
        options = {"kind": workflow, "settings": {"strength": 0.5}}
        if workflow == "vibe":
            options["settings"]["information_extracted"] = 0.6
        creation = CreationInput(
            schema_version=1,
            operation="edit" if workflow == "img2img" else "generate",
            session="work",
            profile=profile.id,
            model=profile.model_default,
            prompt="unchanged prompt",
            count=1,
            inputs=["artifact:" + identity] if workflow == "img2img" else [],
            references=[] if workflow == "img2img" else ["artifact:" + identity],
            reference_options=[] if workflow == "img2img" else [options],
            provider_params={
                "seed": 42,
                **({"strength": 0.5, "noise": 0.1} if workflow == "img2img" else {}),
            },
        )
        status, payload = plan_creation(
            container.repositories,
            container.artifact_store,
            creation,
            private_store=container.private_state_store,
        )
        assert status == "approval_required", payload
        assert not calls
        plan = payload["plan"]
        PlanningService(container.repositories).approve(plan["id"], plan["plan_digest"])
        executor.adapter = NovelAIAdapter(
            profile,
            resolve_credential=lambda _: "offline-test",
            transport=httpx.MockTransport(respond),
        )
        facts = service(container, validator, executor).execute_approved(
            plan["id"],
            plan["plan_digest"],
            model_revision="local",
            input_artifact_ids=(identity,) if workflow == "img2img" else (),
            reference_artifact_ids=() if workflow == "img2img" else (identity,),
            reference_options=() if workflow == "img2img" else (options,),
        )
        assert facts["status"] == "succeeded", facts
        assert facts["job"]["actual_count"] == 1
        assert executor.read_private_response(facts["job"]["id"])["seeds"] == [123]
        recovered = build_request(
            container.repositories,
            container.artifact_store,
            facts["job"],
            private_store=container.private_state_store,
        )
        assert recovered.normalized_parameters["seed"] == 42
        assert recovered.inputs == ((png_bytes,) if workflow == "img2img" else ())
        roles = [r.kind for r in recovered.references]
        assert roles == ([] if workflow == "img2img" else [workflow])
        assert len(calls) == 1
        if workflow == "vibe":
            assert recovered.references[0].prepared_content == b"opaque-private-encoding"
            assert len(encoder.calls) == 1
    # A fresh process/container can reconstruct the frozen dispatch without paid work.
    with Container(workspace=str(tmp_path)) as restarted:
        recovered_again = build_request(
            restarted.repositories,
            restarted.artifact_store,
            facts["job"],
            private_store=restarted.private_state_store,
        )
        assert recovered_again.inputs == recovered.inputs
        assert recovered_again.references == recovered.references
        assert len(calls) == 1
        assert len(encoder.calls) == (1 if workflow == "vibe" else 0)


@pytest.mark.parametrize(
    "operation,params,count",
    [
        ("edit", {}, 1),
        ("edit", {"strength": 0.5, "noise": 0.1}, 2),
        ("generate", {}, 1),
        ("generate", {"strength": 0.5}, 0),
    ],
)
@pytest.mark.parametrize("asynchronous", [False, True])
def test_invalid_shape_never_creates_approval_or_job(
    tmp_path, png_bytes, operation, params, count, asynchronous, monkeypatch
):
    from imagen_cli.application.session_service import SessionService

    with Container(workspace=str(tmp_path)) as container:
        SessionService(container.repositories, container.artifact_store).create_session("work")
        _, profile, identity, encoder = setup_vibe(container, png_bytes)

        def forbidden(*args, **kwargs):
            pytest.fail("invalid shape crossed offline planning boundary")

        monkeypatch.setattr("imagen_cli.execution.configured.resolve_credential", forbidden)
        monkeypatch.setattr(httpx.Client, "send", forbidden)
        creation = CreationInput(
            schema_version=1,
            operation=operation,
            session="work",
            profile=profile.id,
            prompt="test",
            inputs=["artifact:" + identity] * count,
            provider_params=params,
        )
        status, _ = plan_creation(
            container.repositories,
            container.artifact_store,
            creation,
            asynchronous=asynchronous,
            private_store=container.private_state_store,
        )
        assert status == "validation_error"
        for table in ("plans", "approvals", "jobs"):
            assert not container.repositories.list(table)
        from imagen_cli.application.profile_service import ProfileService
        from imagen_cli.cli.commands.create import CreateHandler

        handler = CreateHandler(
            SessionService(container.repositories, container.artifact_store),
            ProfileService(),
            None,
            planning_gate=forbidden,
        )
        assert (
            handler.handle(creation, profile=profile, model_revision="local")[0]
            == "validation_error"
        )
        assert not encoder.calls


@pytest.mark.parametrize("workflow", ["vibe", "precise", "img2img"])
def test_actual_worker_dispatch_once(tmp_path, png_bytes, workflow):
    from unittest.mock import patch

    from imagen_cli.application.session_service import SessionService
    from imagen_cli.execution.worker import Worker, _execute_pending

    with Container(workspace=str(tmp_path)) as c:
        SessionService(c.repositories, c.artifact_store).create_session("work")
        prep, p, aid, encoder = setup_vibe(c, png_bytes)
        if workflow == "vibe":
            planned = prep.plan(p.id, aid, p.model_default, 0.6)
            prep.execute(planned["id"], planned["digest"])
        creation = CreationInput(
            schema_version=1,
            operation="edit" if workflow == "img2img" else "generate",
            session="work",
            profile=p.id,
            prompt="test",
            inputs=["artifact:" + aid] if workflow == "img2img" else [],
            references=[] if workflow == "img2img" else ["artifact:" + aid],
            reference_options=[]
            if workflow == "img2img"
            else [
                {
                    "kind": workflow,
                    "settings": {
                        "strength": 0.5,
                        **({"information_extracted": 0.6} if workflow == "vibe" else {}),
                    },
                }
            ],
            provider_params={
                "seed": 42,
                **({"strength": 0.5, "noise": 0.1} if workflow == "img2img" else {}),
            },
        )
        status, data = plan_creation(
            c.repositories,
            c.artifact_store,
            creation,
            asynchronous=True,
            private_store=c.private_state_store,
        )
        assert status == "accepted", (status, data)
        calls = []

        def respond(req):
            calls.append(json.loads(req.content))
            return httpx.Response(
                200, json={"images": [{"image": base64.b64encode(png_bytes).decode(), "seed": 123}]}
            )

        class Adapter(NovelAIAdapter):
            def __init__(self, profile, **kw):
                super().__init__(
                    profile,
                    resolve_credential=lambda _: "offline-key",
                    transport=httpx.MockTransport(respond),
                )

        worker = Worker(c.repositories)
        worker.start()
        with patch("imagen_cli.providers.novelai.NovelAIAdapter", Adapter):
            _execute_pending(c, worker)
            _execute_pending(c, worker)
        job = c.repositories.get("jobs", data["job"]["id"])
        worker.stop()
        assert job["state"] == "succeeded", job
        assert len(calls) == 1
        assert len(encoder.calls) == (1 if workflow == "vibe" else 0)


@pytest.mark.parametrize("field", ["mask", "native_context"])
def test_shared_shape_refuses_unsupported_context(field):
    from imagen_cli.providers.novelai_parameters import validate_request_shape

    with pytest.raises(ValueError, match="masks or native context"):
        validate_request_shape(operation="generate", primary_count=0, **{field: object()})


def test_plain_generation_and_mask_preflight(tmp_path, png_bytes):
    from imagen_cli.application.session_service import SessionService

    with Container(workspace=str(tmp_path)) as c:
        SessionService(c.repositories, c.artifact_store).create_session("work")
        _, profile, aid, _ = setup_vibe(c, png_bytes)
        creation = CreationInput(
            schema_version=1,
            operation="generate",
            session="work",
            profile=profile.id,
            prompt="test",
            mask="artifact:" + aid,
        )
        assert plan_creation(c.repositories, c.artifact_store, creation)[0] == "validation_error"
        assert not c.repositories.list("plans")
        assert not c.repositories.list("approvals")
        assert not c.repositories.list("jobs")
        creation = creation.model_copy(update={"mask": None})
        assert plan_creation(c.repositories, c.artifact_store, creation)[0] == "approval_required"
