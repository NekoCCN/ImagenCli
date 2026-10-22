"""Persisted plans can be inspected and approved without dispatch."""

import base64
import io
import json

import httpx
from jsonschema import validate

from imagen_cli.application.container import Container
from imagen_cli.application.planning_service import PlanningService
from imagen_cli.application.session_service import SessionService
from imagen_cli.cli.entrypoint import main
from imagen_cli.cli.registry import discover
from tests.contract.test_creation_planning_cli import seed
from tests.integration.test_local_cli import invoke
from tests.support import capability


def test_plan_review_and_approval_are_separate_from_execution(tmp_path):
    workspace = tmp_path / "work"
    prompt = seed(workspace, tmp_path)
    code, result = invoke(workspace, "generate", "--session", "poster", "--prompt-file",
                          str(prompt), "--dry-run")
    assert code == 0
    plan = result["data"]["plan"]
    code, shown = invoke(workspace, "plan", "show", plan["id"])
    assert code == 0
    assert shown["data"]["plan"]["plan_digest"] == plan["plan_digest"]
    assert invoke(workspace, "plan", "approve", plan["id"], "--digest", "0" * 64)[0] == 2
    code, approved = invoke(workspace, "plan", "approve", plan["id"],
                            "--digest", plan["plan_digest"])
    assert code == 0
    assert approved["status"] == "approved"
    assert approved["data"]["approval"]["state"] == "approved"
    with Container(workspace=str(workspace)) as container:
        assert container.repositories.list("jobs") == []
        assert container.repositories.list("turns") == []


def test_plan_retry_rebuilds_a_fresh_request(tmp_path):
    workspace = tmp_path / "work"
    prompt = seed(workspace, tmp_path)
    code, result = invoke(workspace, "generate", "--session", "poster", "--prompt-file",
                          str(prompt), "--dry-run")
    assert code == 0
    original = result["data"]["plan"]
    code, retried = invoke(workspace, "plan", "retry", original["id"])
    assert code == 6, retried
    assert retried["status"] == "approval_required"
    rebuilt = retried["data"]["plan"]
    assert rebuilt["id"] != original["id"]
    assert rebuilt["plan_digest"] != original["plan_digest"]
    assert rebuilt["prompt_snapshot"] == original["prompt_snapshot"]
    assert rebuilt["model"] == original["model"]
    assert rebuilt["normalized_parameters"] == original["normalized_parameters"]
    assert invoke(workspace, "plan", "retry", "pln_" + "1" * 32)[0] == 4


def test_missing_plan_does_not_claim_success(tmp_path):
    assert invoke(tmp_path / "work", "plan", "show", "pln_" + "1" * 32)[0] == 4


def test_cli_executes_approved_plan_once_with_real_adapter(tmp_path, png_bytes, monkeypatch):
    workspace = tmp_path / "work"
    seed(workspace, tmp_path)
    monkeypatch.setenv("IMAGEN_TEST_CREDENTIAL", "fixture-secret")
    with Container(workspace=str(workspace)) as container:
        repo = container.repositories
        repo.update("profiles", "local", {"credential_ref": "env:IMAGEN_TEST_CREDENTIAL"})
        session = SessionService(repo, container.artifact_store).create_session("execution")
        record = capability(id="execution", model="gpt-image-1", parameter_schema={
            "type": "object", "properties": {"count": {"const": 1}},
            "additionalProperties": False,
        })
        repo.insert("capabilities", record)
        planning = PlanningService(repo)
        plan, _ = planning.create_plan(
            session_id=session.id, operation="generate", model="gpt-image-1",
            normalized_parameters={"count": 1}, prompt_snapshot="blue sky", input_hashes=[],
            portable_context={"kind": "none"}, capability_digest=record["digest"],
            target_snapshot={
                "profile_identity": "local", "profile_revision": 1,
                "endpoint_origin": "https://example.test/v1",
                "allowed_upload_targets": ["https://example.test/v1"],
                "provider": "openai", "family": "images", "model_revision": "2026-01",
            },
        )
        planning.approve(plan.id, plan.plan_digest)
    requests = []

    def handle(request):
        requests.append(request)
        assert request.url == "https://example.test/v1/images/generations"
        assert request.headers["authorization"] == "Bearer fixture-secret"
        assert json.loads(request.content)["prompt"] == "blue sky"
        return httpx.Response(200, json={"data": [{
            "b64_json": base64.b64encode(png_bytes).decode(),
        }]})

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **kwargs: httpx.MockTransport(handle))

    def run():
        out = io.StringIO()
        code = main(["--workspace", str(workspace), "--output", "json", "plan", "execute", plan.id],
                    stdout=out, stderr=io.StringIO())
        return code, json.loads(out.getvalue())

    code, first = run()
    assert code == 0, first
    assert first["status"] == "succeeded"
    assert len(first["data"]["artifacts"]) == 1
    monkeypatch.delenv("IMAGEN_TEST_CREDENTIAL")
    with Container(workspace=str(workspace)) as container:
        container.repositories.update("profiles", "local", {"revision": 2})
    assert run() == (code, first)
    assert len(requests) == 1
    with Container(workspace=str(workspace)) as container:
        assert len(container.repositories.list("jobs")) == 1
        assert container.artifact_store.get(first["data"]["artifacts"][0]["id"]).read_bytes() == (
            png_bytes
        )


def test_cli_generate_resume_and_edit_preserves_history(tmp_path, png_bytes, monkeypatch):
    workspace = tmp_path / "work"
    prompt = seed(workspace, tmp_path)
    monkeypatch.setenv("IMAGEN_TEST_CREDENTIAL", "fixture-secret")
    with Container(workspace=str(workspace)) as container:
        container.repositories.update("profiles", "local", {
            "credential_ref": "env:IMAGEN_TEST_CREDENTIAL",
        })
        for operation in ("generate", "edit"):
            container.repositories.insert("capabilities", capability(
                id=f"journey-{operation}", model="gpt-image-1", operation=operation,
                continuation_strategy="explicit_images" if operation == "edit" else "none",
                parameter_schema={"type": "object", "properties": {"count": {"const": 1}},
                                  "additionalProperties": False},
            ))
    requests = []

    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            assert request.url.path == "/v1/images/generations"
        else:
            assert request.url.path == "/v1/images/edits"
            assert png_bytes in request.content
        return httpx.Response(200, json={"data": [{
            "b64_json": base64.b64encode(png_bytes).decode(),
        }]})

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **kwargs: httpx.MockTransport(handle))

    def execute(plan_id):
        out = io.StringIO()
        code = main(["--workspace", str(workspace), "--output", "json", "plan", "execute", plan_id],
                    stdout=out, stderr=io.StringIO())
        return code, json.loads(out.getvalue())

    def create(operation, *inputs):
        code, planned = invoke(workspace, operation, "--session", "poster", "--model",
                               "gpt-image-1", "--prompt-file", str(prompt), *inputs)
        assert code == 6, planned
        plan = planned["data"]["plan"]
        # Pending approval must not create a job or send anything.
        assert execute(plan["id"])[0] == 2
        assert invoke(workspace, "plan", "approve", plan["id"], "--digest",
                      plan["plan_digest"])[0] == 0
        code, executed = execute(plan["id"])
        assert code == 0, executed
        validate(executed, discover("plan execute")["output_schema"])
        assert execute(plan["id"]) == (code, executed)
        return executed["data"]

    first = create("generate")
    code, restored = invoke(workspace, "session", "resume", "poster")
    assert code == 0, restored
    assert restored["data"]["session"]["current_turn_id"] == first["turn"]["id"]
    second = create("edit", "--input", f"artifact:{first['artifacts'][0]['id']}")
    assert second["turn"]["parent_turn_id"] == first["turn"]["id"]
    code, restored = invoke(workspace, "session", "resume", "poster")
    assert code == 0
    assert restored["data"]["session"]["current_turn_id"] == second["turn"]["id"]
    assert len(restored["data"]["turns"]) == 2
    assert len(requests) == 2
    for data in (first, second):
        assert invoke(workspace, "job", "get", data["job"]["id"])[0] == 0
        assert invoke(workspace, "job", "fetch", data["job"]["id"])[0] == 0
