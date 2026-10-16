"""T057: OpenAI Responses image tool is an independent adapter, not OpenAI Images."""

import base64
import json
from dataclasses import replace

import httpx
import pytest

from imagen_cli.domain.models import ProviderProfile
from imagen_cli.providers.base import ProviderFailure, RemoteObservation, ValidatedRequest
from imagen_cli.providers.openai_responses import OpenAIResponsesAdapter


def profile() -> ProviderProfile:
    return ProviderProfile(
        id="responses-local", name="offline", provider="openai", family="responses",
        base_url="https://api.openai.com/v1", model_default="gpt-image-1",
        credential_ref="env:FIXTURE", trust_policy={}, revision=1,
    )


def request(
    target: ProviderProfile, *, operation: str = "generate",
    inputs: tuple[bytes, ...] = (), authorized: bool = True,
) -> ValidatedRequest:
    return ValidatedRequest(
        plan_id="pln_" + "1" * 32, job_id="job_" + "2" * 32,
        profile_identity=target.id, profile_revision=target.revision,
        operation=operation, model=target.model_default, model_revision="2026-10",
        prompt="a red kite", normalized_parameters={"size": "1024x1024"},
        capability_digest="a" * 64, authorized=authorized,
        dispatch_intent_recorded=True, inputs=inputs,
    )


def test_responses_generate_uses_output_items_and_never_images_route(png_bytes: bytes) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST"
        assert req.url.path == "/v1/responses"
        assert "/images/" not in req.url.path
        assert req.headers["authorization"] == "Bearer fixture-secret"
        body = json.loads(req.content)
        assert body["model"] == "gpt-image-1"
        assert body["tools"][0]["type"] == "image_generation"
        assert body["tools"][0]["size"] == "1024x1024"
        assert body["input"][0]["content"][0]["type"] == "input_text"
        return httpx.Response(200, json={
            "id": "resp_1", "status": "completed",
            "output": [{
                "type": "image_generation_call", "status": "completed",
                "result": base64.b64encode(png_bytes).decode("ascii"),
            }],
            "usage": {"total_tokens": 3},
        })

    target = profile()
    adapter = OpenAIResponsesAdapter(
        target, transport=httpx.MockTransport(handler),
        resolve_credential=lambda ref: "fixture-secret",
    )
    result = adapter.create(request(target))
    assert result.artifacts[0].content == png_bytes
    assert adapter.cancel(object()).state == "unsupported"
    with pytest.raises(ProviderFailure, match="unsupported"):
        adapter.poll(object())
    with pytest.raises(ProviderFailure, match="unsupported"):
        adapter.fetch(RemoteObservation("succeeded"))


def test_responses_edit_sends_only_explicit_image_parts(png_bytes: bytes) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        parts = body["input"][0]["content"]
        assert [part["type"] for part in parts] == ["input_text", "input_image"]
        assert parts[1]["image_url"].startswith("data:image/png;base64,")
        return httpx.Response(200, json={
            "id": "resp_2", "status": "completed",
            "output": [{
                "type": "image_generation_call", "status": "completed",
                "result": base64.b64encode(png_bytes).decode("ascii"),
            }],
        })

    target = profile()
    adapter = OpenAIResponsesAdapter(
        target, transport=httpx.MockTransport(handler),
        resolve_credential=lambda ref: "fixture-secret",
    )
    result = adapter.create(request(target, operation="edit", inputs=(png_bytes,)))
    assert result.artifacts[0].content == png_bytes


def test_responses_never_sends_before_approval_and_rejects_native_context(
    png_bytes: bytes,
) -> None:
    def forbidden(req: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    target = profile()
    adapter = OpenAIResponsesAdapter(
        target, transport=httpx.MockTransport(forbidden),
        resolve_credential=lambda ref: "fixture-secret",
    )
    with pytest.raises(ProviderFailure, match="approval_required"):
        adapter.create(request(target, authorized=False))
    with pytest.raises(ProviderFailure, match="unsupported"):
        adapter.create(replace(request(target), native_context=object()))


def test_responses_missing_image_output_is_a_provider_error() -> None:
    target = profile()
    adapter = OpenAIResponsesAdapter(
        target,
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"status": "completed", "output": []})
        ),
        resolve_credential=lambda ref: "fixture-secret",
    )
    with pytest.raises(ProviderFailure) as failure:
        adapter.create(request(target))
    assert failure.value.type == "provider_error"
