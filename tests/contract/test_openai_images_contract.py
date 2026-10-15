"""T028: real adapter, offline HTTP boundary, no credentials or paid creations."""

import base64
import json
from dataclasses import replace
from typing import Any

import httpx
import pytest

from imagen_cli.domain.models import ProviderProfile
from imagen_cli.providers.openai_images import (
    OpenAIImagesAdapter,
    ProviderFailure,
    ValidatedRequest,
)


@pytest.fixture
def profile() -> ProviderProfile:
    return ProviderProfile(
        id="openai-local",
        name="offline",
        provider="openai",
        family="images",
        base_url="https://api.openai.com/v1",
        model_default="gpt-image-1",
        credential_ref="fixture:only",
        trust_policy={},
        revision=1,
    )


@pytest.fixture
def creation() -> ValidatedRequest:
    return ValidatedRequest(
        plan_id="pln_" + "1" * 32,
        job_id="job_" + "2" * 32,
        profile_identity="openai-local",
        profile_revision=1,
        operation="generate",
        model="gpt-image-1",
        model_revision="fixture-revision",
        prompt="画一只猫",
        normalized_parameters={"count": 2, "size": "1024x1024"},
        capability_digest="a" * 64,
        authorized=True,
        dispatch_intent_recorded=True,
    )


def adapter(profile: ProviderProfile, handler: Any, credentials: Any = None, **limits: Any) -> Any:
    return OpenAIImagesAdapter(
        profile,
        transport=httpx.MockTransport(handler),
        resolve_credential=credentials or (lambda ref: "fixture-secret"),
        **limits,
    )


def payload(png: bytes, count: int = 2) -> dict[str, Any]:
    return {
        "created": 1792713600,
        "data": [
            {"b64_json": base64.b64encode(png).decode(), "revised_prompt": "private"}
            for _ in range(count)
        ],
        "usage": {"total_tokens": 42},
        "background": "opaque",
        "output_format": "png",
    }


def test_generate_normalizes_and_extracts_independent_candidates(
    profile: ProviderProfile,
    creation: ValidatedRequest,
    png_bytes: bytes,
) -> None:
    calls = []

    def respond(sent: httpx.Request) -> httpx.Response:
        calls.append(sent)
        assert str(sent.url) == "https://api.openai.com/v1/images/generations"
        assert sent.headers["authorization"] == "Bearer fixture-secret"
        assert json.loads(sent.content) == {
            "model": "gpt-image-1",
            "prompt": "画一只猫",
            "n": 2,
            "size": "1024x1024",
            "quality": "high",
            "output_format": "png",
        }
        return httpx.Response(200, json=payload(png_bytes))

    creation = replace(
        creation,
        normalized_parameters={
            **creation.normalized_parameters,
            "quality": "high",
            "output_format": "png",
        },
    )
    result = adapter(profile, respond).create(creation)
    assert len(calls) == 1
    assert result.requested_count == result.actual_count == 2
    assert result.artifacts[0] is not result.artifacts[1]
    assert result.artifacts[0].id != result.artifacts[1].id
    assert [item.index for item in result.artifacts] == [0, 1]
    assert all(
        item.content == png_bytes and item.media_type == "image/png" for item in result.artifacts
    )
    assert result.usage == {"total_tokens": 42}
    assert result.private_response["created"] == 1792713600
    assert result.private_response["data"][0]["revised_prompt"] == "private"
    assert len(result.private_response["data"]) == 2
    assert "private" not in repr(result)
    assert base64.b64encode(png_bytes).decode() not in repr(result)


def test_edit_uses_frozen_ordered_images_and_mask(
    profile: ProviderProfile,
    creation: ValidatedRequest,
    png_bytes: bytes,
) -> None:
    def respond(sent: httpx.Request) -> httpx.Response:
        assert sent.url.path == "/v1/images/edits"
        body = sent.content
        assert body.index(b"first-image") < body.index(b"second-image")
        assert body.count(b'name="image[]"') == 2
        assert b'name="mask"' in body and b"mask-image" in body
        assert b'name="input_fidelity"' in body and b"high" in body
        assert b"/home/" not in body
        return httpx.Response(200, json=payload(png_bytes, 1))

    edit = replace(
        creation,
        operation="edit",
        inputs=(b"first-image", b"second-image"),
        mask=b"mask-image",
        normalized_parameters={"input_fidelity": "high"},
    )
    result = adapter(profile, respond).create(edit)
    assert result.actual_count == 1


def test_edit_accepts_default_fidelity_and_preserves_explicit_images(
    profile: ProviderProfile,
    creation: ValidatedRequest,
    png_bytes: bytes,
) -> None:
    def respond(sent: httpx.Request) -> httpx.Response:
        assert b'name="input_fidelity"' not in sent.content
        return httpx.Response(200, json=payload(png_bytes, 1))

    edited = replace(creation, operation="edit", inputs=(png_bytes,), normalized_parameters={})
    assert adapter(profile, respond).create(edited).actual_count == 1


@pytest.mark.parametrize(
    "changes,error",
    [
        ({"authorized": False}, "approval_required"),
        ({"dispatch_intent_recorded": False}, "validation_error"),
        ({"profile_revision": 2}, "validation_error"),
        ({"profile_identity": "other"}, "validation_error"),
        ({"model": "invented-model"}, "unsupported_capability"),
        ({"normalized_parameters": {"invented": "value"}}, "validation_error"),
        ({"normalized_parameters": {"quality": "invalid"}}, "validation_error"),
        ({"normalized_parameters": {"background": "invalid"}}, "validation_error"),
        ({"normalized_parameters": {"moderation": "invalid"}}, "validation_error"),
        ({"normalized_parameters": {"output_compression": True}}, "validation_error"),
        ({"normalized_parameters": {"output_compression": 101}}, "validation_error"),
        ({"native_context": object()}, "unsupported_capability"),
        ({"prompt": " "}, "validation_error"),
        ({"normalized_parameters": {"model": "override"}}, "validation_error"),
        ({"normalized_parameters": {"count": True}}, "validation_error"),
        ({"normalized_parameters": {"size": "bad"}}, "validation_error"),
        (
            {"normalized_parameters": {"background": "transparent", "output_format": "jpeg"}},
            "validation_error",
        ),
        ({"operation": "edit"}, "validation_error"),
        ({"inputs": (b"unexpected",)}, "validation_error"),
        ({"mask": b"unexpected"}, "validation_error"),
        ({"normalized_parameters": {"input_fidelity": "high"}}, "validation_error"),
    ],
)
def test_rejects_before_credentials_or_upload(
    profile: ProviderProfile,
    creation: ValidatedRequest,
    changes: dict[str, Any],
    error: str,
) -> None:
    def forbidden(*args: Any) -> Any:
        pytest.fail("rejected creation must not resolve credentials or send HTTP")

    with pytest.raises(ProviderFailure) as caught:
        adapter(profile, forbidden, forbidden).create(replace(creation, **changes))
    assert caught.value.type == error
    assert not caught.value.accepted_maybe


@pytest.mark.parametrize("count", [1, 3])
def test_preserves_actual_count_without_filling_or_truncating(
    profile: ProviderProfile,
    creation: ValidatedRequest,
    png_bytes: bytes,
    count: int,
) -> None:
    result = adapter(profile, lambda _: httpx.Response(200, json=payload(png_bytes, count))).create(
        creation
    )
    assert result.requested_count == 2
    assert result.actual_count == count
    assert len({item.id for item in result.artifacts}) == count


@pytest.mark.parametrize(
    "status,code,kind,accepted",
    [
        (400, "bad_creation", "validation_error", False),
        (401, "invalid_api_key", "authentication_error", False),
        (403, "permission", "permission_denied", False),
        (400, "content_policy_violation", "safety_refusal", False),
        (429, "rate_limit_exceeded", "provider_error", False),
        (500, "server_error", "provider_error", True),
        (307, "redirect", "outcome_unknown", True),
    ],
)
def test_http_failures_are_safe_and_never_replayed(
    profile: ProviderProfile,
    creation: ValidatedRequest,
    status: int,
    code: str,
    kind: str,
    accepted: bool,
) -> None:
    calls = []

    def respond(sent: httpx.Request) -> httpx.Response:
        calls.append(sent)
        return httpx.Response(
            status,
            json={"error": {"code": code, "message": "SECRET"}},
            headers={"location": "https://attacker.invalid/SECRET"},
        )

    with pytest.raises(ProviderFailure) as caught:
        adapter(profile, respond).create(creation)
    assert caught.value.type == kind
    assert caught.value.accepted_maybe is accepted
    assert len(calls) == 1
    assert "SECRET" not in str(caught.value)
    assert "attacker" not in repr(caught.value)
    assert caught.value.__cause__ is None


def test_disconnect_is_unknown_and_not_retried(
    profile: ProviderProfile,
    creation: ValidatedRequest,
) -> None:
    calls = []

    def disconnect(sent: httpx.Request) -> httpx.Response:
        calls.append(sent)
        raise httpx.ReadError("SECRET", request=sent)

    with pytest.raises(ProviderFailure) as caught:
        adapter(profile, disconnect).create(creation)
    assert caught.value.type == "outcome_unknown"
    assert caught.value.accepted_maybe
    assert len(calls) == 1
    assert "SECRET" not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    "response",
    [
        {"data": []},
        {"data": [{"b64_json": "!!!"}]},
        {"data": [{"url": "http://127.0.0.1/SECRET"}]},
        {"data": [{"url": "https://attacker.invalid/SECRET"}]},
        {"data": "wrong"},
        {"data": [{"b64_json": ""}]},
        {"data": [None]},
    ],
)
def test_malformed_or_url_only_results_never_trigger_downloads(
    profile: ProviderProfile,
    creation: ValidatedRequest,
    response: dict[str, Any],
) -> None:
    calls = []

    def respond(sent: httpx.Request) -> httpx.Response:
        calls.append(sent)
        return httpx.Response(200, json=response)

    with pytest.raises(ProviderFailure) as caught:
        adapter(profile, respond).create(creation)
    assert caught.value.type == "provider_error"
    assert caught.value.accepted_maybe
    assert len(calls) == 1
    assert "SECRET" not in str(caught.value)


def test_bounds_response_and_decoded_image(
    profile: ProviderProfile,
    creation: ValidatedRequest,
    png_bytes: bytes,
) -> None:
    for limits in ({"max_response_bytes": 16}, {"max_image_bytes": 8}):
        with pytest.raises(ProviderFailure) as caught:
            adapter(
                profile, lambda _: httpx.Response(200, json=payload(png_bytes)), **limits
            ).create(creation)
        assert caught.value.type == "provider_error"
        assert caught.value.accepted_maybe


def test_streaming_limit_stops_without_reading_entire_response(
    profile: ProviderProfile,
    creation: ValidatedRequest,
) -> None:
    class LargeResponse(httpx.SyncByteStream):
        def __iter__(self) -> Any:
            yield b"x" * 17
            pytest.fail("must stop reading at hard response limit")

    with pytest.raises(ProviderFailure) as caught:
        adapter(
            profile, lambda _: httpx.Response(200, stream=LargeResponse()), max_response_bytes=16
        ).create(creation)
    assert caught.value.accepted_maybe


def test_credential_failure_is_safe_and_does_not_send(
    profile: ProviderProfile,
    creation: ValidatedRequest,
) -> None:
    def credentials(ref: str) -> str:
        raise RuntimeError("SECRET")

    def forbidden(sent: httpx.Request) -> httpx.Response:
        pytest.fail("credential resolution failed")

    with pytest.raises(ProviderFailure) as caught:
        adapter(profile, forbidden, credentials).create(creation)
    assert caught.value.type == "authentication_error"
    assert not caught.value.accepted_maybe
    assert "SECRET" not in str(caught.value)


def test_rejects_input_limits_before_resolving_credentials(
    profile: ProviderProfile,
    creation: ValidatedRequest,
) -> None:
    def forbidden(*args: Any) -> Any:
        pytest.fail("oversized input must not resolve credentials or upload")

    edited = replace(creation, operation="edit", inputs=(b"12345",))
    with pytest.raises(ProviderFailure) as caught:
        adapter(profile, forbidden, forbidden, max_image_bytes=4).create(edited)
    assert not caught.value.accepted_maybe


def test_encoded_image_limit_is_checked_before_base64_decode(
    profile: ProviderProfile,
    creation: ValidatedRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("oversized base64 must not be decoded")

    monkeypatch.setattr(base64, "b64decode", forbidden)
    with pytest.raises(ProviderFailure) as caught:
        adapter(
            profile,
            lambda _: httpx.Response(200, json={"data": [{"b64_json": "YQ==" * 10}]}),
            max_image_bytes=4,
        ).create(creation)
    assert caught.value.accepted_maybe


def test_rejects_non_image_pixel_or_frame_limits(
    profile: ProviderProfile,
    creation: ValidatedRequest,
    png_bytes: bytes,
) -> None:
    with pytest.raises(ProviderFailure):
        adapter(
            profile, lambda _: httpx.Response(200, json=payload(png_bytes)), max_pixels=5
        ).create(creation)


def test_creation_snapshot_does_not_change_with_callers_mutable_parameters(
    creation: ValidatedRequest,
) -> None:
    parameters = {"count": 1}
    frozen = replace(creation, normalized_parameters=parameters)
    parameters["count"] = 9
    assert frozen.normalized_parameters["count"] == 1
    with pytest.raises(TypeError):
        frozen.normalized_parameters["count"] = 3  # type: ignore[index]
