"""Configured foreground waits reach HTTP without adding create retries."""

import base64
import json
from pathlib import Path

import httpx

from imagen_cli.application.container import Container
from imagen_cli.cli.entrypoint import _parse
from imagen_cli.execution.configured import ConfiguredAdapter
from tests.integration.test_approved_execution import execute, service, setup


def test_plan_execute_accepts_explicit_timeout():
    _, _, options = _parse(["plan", "execute", "pln_" + "1" * 32, "--timeout", "240"])
    assert options.timeout == 240


def test_configured_timeout_reaches_single_http_dispatch(tmp_path, png_bytes, monkeypatch):
    sent = []

    def respond(request):
        sent.append(request)
        assert request.extensions["timeout"] == {
            "connect": 10.0, "read": 240.0, "write": 60.0, "pool": 60.0,
        }
        return httpx.Response(200, json={"data": [
            {"b64_json": base64.b64encode(png_bytes).decode()},
        ]})

    monkeypatch.setenv("IMAGEN_TEST_KEY", "fixture-secret")
    monkeypatch.setattr(httpx, "HTTPTransport", lambda **kwargs: httpx.MockTransport(respond))
    with Container(workspace=str(tmp_path)) as container:
        plan, _, validator, executor, _, ids = setup(container, png_bytes)
        container.repositories.update("profiles", "offline", {
            "credential_ref": "env:IMAGEN_TEST_KEY", "model_default": "image-model",
        })
        executor.adapter = ConfiguredAdapter(container.repositories, timeout=240)
        result = execute(service(container, validator, executor), plan, ids)
        assert result["status"] == "succeeded"
        assert execute(service(container, validator, executor), plan, ids) == result
        assert len(sent) == 1
        assert json.loads(sent[0].content)["model"] == "image-model"


def test_cli_execution_returns_usable_artifact_paths(tmp_path, png_bytes, monkeypatch):
    from tests.integration.test_approved_execution import OfflineAdapter
    from tests.unit.test_capabilities import capability

    with Container(workspace=str(tmp_path)) as container:
        plan, _, _, _, _, _ = setup(container, png_bytes)
        container.repositories.insert("capabilities", capability(
            parameter_schema={"type": "object", "properties": {"count": {"const": 1}},
                              "additionalProperties": False},
            continuation_strategy="none",
        ))
        adapter = OfflineAdapter(png_bytes)
        monkeypatch.setattr(
            ConfiguredAdapter, "create", lambda self, request: adapter.create(request),
        )
        status, data = container.execute("plan execute", {"plan_id": plan.id})
        assert status == "succeeded"

        assert Path(data["artifacts"][0]["path"]).read_bytes() == png_bytes
        assert container.execute("plan execute", {"plan_id": plan.id}) == (status, data)
        assert len(adapter.requests) == 1
