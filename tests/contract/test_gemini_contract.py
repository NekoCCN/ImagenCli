"""T029: Gemini contract exercised only with offline HTTP transports."""

import base64
import importlib
import json
from dataclasses import replace
from typing import Any

import httpx
import pytest

from imagen_cli.domain.models import ProviderProfile
from imagen_cli.storage.private_state import PrivateStateStore


def api() -> Any:
    try:
        return importlib.import_module("imagen_cli.providers.gemini")
    except ModuleNotFoundError:
        pytest.fail("T035 Gemini adapter is not implemented")


def test_adapter_is_available() -> None:
    assert callable(api().GeminiAdapter)


@pytest.fixture
def setup(tmp_path: Any) -> Any:
    module = api()
    profile = ProviderProfile(
        id="gemini-local", name="offline", provider="gemini", family="generate_content",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model_default="gemini-2.5-flash-image", credential_ref="fixture:only",
        trust_policy={}, revision=1,
    )
    request = module.ValidatedRequest(
        plan_id="pln_" + "1" * 32, job_id="job_" + "2" * 32,
        profile_identity=profile.id, profile_revision=1,
        operation="generate", model=profile.model_default, model_revision="fixture",
        prompt="画猫", normalized_parameters={}, capability_digest="a" * 64,
        authorized=True, dispatch_intent_recorded=True,
    )
    store = PrivateStateStore(tmp_path / "private")

    def build(handler: Any, **kwargs: Any) -> Any:
        return module.GeminiAdapter(
            profile, private_store=store, transport=httpx.MockTransport(handler),
            resolve_credential=lambda _: "fixture-secret", **kwargs,
        )

    return module, profile, request, store, build


def response(png: bytes, count: int = 1) -> dict[str, Any]:
    return {
        "candidates": [{"content": {"role": "model", "parts": [
            {"text": "private explanation"},
            *[{"inlineData": {"mimeType": "image/png",
                               "data": base64.b64encode(png).decode()},
               "thoughtSignature": "opaque-SECRET"} for _ in range(count)],
        ]}, "finishReason": "STOP"}],
        "usageMetadata": {"totalTokenCount": 42}, "modelVersion": "fixture",
    }


def test_generate_extracts_parts_and_persists_private_context(setup: Any, png_bytes: bytes) -> None:
    _, _, request, store, build = setup
    calls = []

    def send(sent: httpx.Request) -> httpx.Response:
        calls.append(sent)
        assert str(sent.url) == (
            "https://generativelanguage.googleapis.com/v1beta/"
            "models/gemini-2.5-flash-image:generateContent"
        )
        assert sent.headers["x-goog-api-key"] == "fixture-secret"
        assert json.loads(sent.content) == {
            "contents": [{"role": "user", "parts": [{"text": "画猫"}]}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
        }
        return httpx.Response(200, json=response(png_bytes, 2))

    result = build(send).create(request)
    assert result.actual_count == 2
    assert len({artifact.id for artifact in result.artifacts}) == 2
    assert [artifact.content for artifact in result.artifacts] == [png_bytes, png_bytes]
    assert result.usage == {"totalTokenCount": 42}
    assert "opaque-SECRET" not in repr(result)
    assert base64.b64encode(png_bytes).decode() not in repr(result)
    assert store.read(result.context.identity, result.context.sha256)
    assert len(calls) == 1


def test_private_save_failure_does_not_retry_remote_creation(
    setup: Any, png_bytes: bytes, monkeypatch: Any,
) -> None:
    module, _, request, store, build = setup
    calls = []

    def send(sent: httpx.Request) -> httpx.Response:
        calls.append(sent)
        return httpx.Response(200, json=response(png_bytes))

    def disk_full(*args: Any) -> Any:
        raise OSError("SECRET path")

    monkeypatch.setattr(store, "write", disk_full)
    with pytest.raises(module.ProviderFailure) as caught:
        build(send).create(request)
    assert caught.value.type == "save_failed"
    assert caught.value.accepted_maybe
    assert "SECRET" not in str(caught.value)
    assert len(calls) == 1


def test_fresh_adapter_restores_native_parts_exactly(setup: Any, png_bytes: bytes) -> None:
    _, _, request, _, build = setup
    first = build(lambda _: httpx.Response(200, json=response(png_bytes))).create(request)
    restored = build(lambda _: pytest.fail("restore must be local")).restore_context(
        first.context, request
    )
    assert restored.available
    assert restored.contents[-1] == response(png_bytes)["candidates"][0]["content"]

    def edit(sent: httpx.Request) -> httpx.Response:
        contents = json.loads(sent.content)["contents"]
        assert contents[:-1] == restored.contents
        assert contents[-1] == {"role": "user", "parts": [
            {"text": "改蓝"},
            {"inlineData": {"mimeType": "image/png",
                            "data": base64.b64encode(png_bytes).decode()}},
        ]}
        return httpx.Response(200, json=response(png_bytes))

    result = build(edit).create(replace(
        request, job_id="job_" + "3" * 32, operation="edit", prompt="改蓝",
        inputs=(png_bytes,), native_context=first.context,
    ))
    assert result.actual_count == 1


@pytest.mark.parametrize("change", [
    {"profile_identity": "other"}, {"profile_revision": 2},
    {"model": "different-model"}, {"model_revision": "different-revision"},
])
def test_native_context_target_mismatch_is_explicit_loss(
    setup: Any, png_bytes: bytes, change: dict[str, Any],
) -> None:
    _, _, request, _, build = setup
    first = build(lambda _: httpx.Response(200, json=response(png_bytes))).create(request)
    restored = build(lambda _: pytest.fail("restore must be local")).restore_context(
        first.context, replace(request, **change)
    )
    assert not restored.available
    assert restored.context_loss
    assert restored.contents == []


def test_missing_required_state_never_silently_generates(setup: Any, png_bytes: bytes) -> None:
    module, _, request, store, build = setup
    first = build(lambda _: httpx.Response(200, json=response(png_bytes))).create(request)
    (store.root / (first.context.identity + ".bin")).unlink()
    adapter = build(lambda _: pytest.fail("no fallback or create after context loss"))
    assert not adapter.restore_context(first.context, request).available
    with pytest.raises(module.ProviderFailure) as caught:
        adapter.create(replace(request, operation="edit", native_context=first.context))
    assert caught.value.type == "context_loss"
    assert not caught.value.accepted_maybe


@pytest.mark.parametrize("changes", [
    {"authorized": False}, {"authorized": "yes"},
    {"dispatch_intent_recorded": False}, {"dispatch_intent_recorded": 1},
    {"normalized_parameters": {"aspect_ratio": []}},
    {"profile_revision": 2}, {"model": "openai-model"},
    {"normalized_parameters": {"unknown": True}},
    {"normalized_parameters": {"count": True}},
    {"normalized_parameters": {"count": 2}},
    {"operation": "edit"}, {"inputs": (b"unexpected",)},
])
def test_preflight_rejects_without_network(setup: Any, changes: dict[str, Any]) -> None:
    module, _, request, _, build = setup
    with pytest.raises(module.ProviderFailure) as caught:
        build(lambda _: pytest.fail("preflight must not send")).create(replace(request, **changes))
    assert not caught.value.accepted_maybe


@pytest.mark.parametrize("status,kind,accepted", [
    (401, "authentication_error", False), (403, "permission_denied", False),
    (400, "validation_error", False), (429, "provider_error", False),
    (500, "provider_error", True), (307, "outcome_unknown", True),
])
def test_http_failures_are_redacted_and_not_replayed(
    setup: Any, status: int, kind: str, accepted: bool,
) -> None:
    module, _, request, _, build = setup
    calls = []

    def send(sent: httpx.Request) -> httpx.Response:
        calls.append(sent)
        return httpx.Response(status, json={"error": {"message": "SECRET"}},
                              headers={"location": "https://attacker.invalid/SECRET"})

    with pytest.raises(module.ProviderFailure) as caught:
        build(send).create(request)
    assert caught.value.type == kind
    assert caught.value.accepted_maybe is accepted
    assert "SECRET" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert len(calls) == 1


def test_disconnect_is_unknown_and_same_job_cannot_create_again(setup: Any) -> None:
    module, _, request, _, build = setup
    calls = []

    def disconnect(sent: httpx.Request) -> Any:
        calls.append(sent)
        raise httpx.ReadError("SECRET", request=sent)

    adapter = build(disconnect)
    for _ in range(2):
        with pytest.raises(module.ProviderFailure) as caught:
            adapter.create(request)
        assert caught.value.accepted_maybe
        assert caught.value.type == "outcome_unknown"
    assert len(calls) == 1


@pytest.mark.parametrize("body,kind", [
    ({"promptFeedback": {"blockReason": "SAFETY"}}, "safety_refusal"),
    ({"candidates": [{"finishReason": "SAFETY"}]}, "safety_refusal"),
    ({"candidates": []}, "provider_error"),
    ({"candidates": [{"content": {"parts": [{"text": "no image"}]}}]}, "provider_error"),
    ({"candidates": [{"content": {"parts": [{"fileData": {
        "fileUri": "http://127.0.0.1/SECRET"}}]}}]}, "provider_error"),
    ({"candidates": [{"content": {"parts": [{"inlineData": {
        "mimeType": "image/png", "data": "!!!"}}]}}]}, "provider_error"),
])
def test_refusal_and_invalid_results_do_not_fetch_or_fallback(
    setup: Any, body: dict[str, Any], kind: str,
) -> None:
    module, _, request, _, build = setup
    calls = []

    def send(sent: httpx.Request) -> httpx.Response:
        calls.append(sent)
        return httpx.Response(200, json=body)

    with pytest.raises(module.ProviderFailure) as caught:
        build(send).create(request)
    assert caught.value.type == kind
    assert len(calls) == 1


def test_response_and_image_limits(setup: Any, png_bytes: bytes) -> None:
    module, _, request, _, build = setup
    for limits in ({"max_response_bytes": 16}, {"max_image_bytes": 8}):
        with pytest.raises(module.ProviderFailure) as caught:
            build(lambda _: httpx.Response(200, json=response(png_bytes)), **limits).create(request)
        assert caught.value.accepted_maybe


def test_endpoint_trust_allows_configured_https_or_loopback_only():
    from imagen_cli.providers.gemini import _trusted_endpoint

    for url in (
        "https://generativelanguage.googleapis.com/v1beta",
        "https://gateway.example:8443/gemini/v1beta/",
        "http://127.0.0.1:3000/v1beta", "http://localhost:3000/v1beta",
    ):
        assert _trusted_endpoint(url)
    for url in (
        "http://evil.example/v1beta", "https://gateway.example/v1",
        "https:///v1beta", "https://gateway.example:bad/v1beta",
        "https://user:password@gateway.example/v1beta",
        "https://user@gateway.example/v1beta",
        "https://gateway.example/v1beta?key=secret",
        "https://gateway.example/v1beta#secret",
    ):
        assert not _trusted_endpoint(url)


@pytest.mark.parametrize("model", ["nano-banana-2", "gateway/nano-banana-2"])
@pytest.mark.parametrize("operation", ["generate", "edit"])
def test_nano_banana_alias_uses_configured_https_endpoint(
    setup: Any, png_bytes: bytes, model: str, operation: str,
) -> None:
    module, profile, request, store, _ = setup
    profile = profile.model_copy(update={
        "base_url": "https://gateway.example/gemini/v1beta", "model_default": model,
    })
    request = replace(request, model=model, operation=operation,
                      inputs=(png_bytes,) if operation == "edit" else ())
    calls = []

    def send(sent: httpx.Request) -> httpx.Response:
        calls.append(sent)
        assert str(sent.url) == (
            f"https://gateway.example/gemini/v1beta/models/{model}:generateContent"
        )
        assert sent.headers["x-goog-api-key"] == "fixture-secret"
        parts = json.loads(sent.content)["contents"][0]["parts"]
        assert parts[0] == {"text": "画猫"}
        assert len(parts) == (2 if operation == "edit" else 1)
        if operation == "edit":
            assert base64.b64decode(parts[1]["inlineData"]["data"]) == png_bytes
        return httpx.Response(200, json=response(png_bytes))

    result = module.GeminiAdapter(
        profile, private_store=store, transport=httpx.MockTransport(send),
        resolve_credential=lambda _: "fixture-secret",
    ).create(request)
    assert result.artifacts[0].content == png_bytes
    assert result.context is not None
    assert len(calls) == 1
