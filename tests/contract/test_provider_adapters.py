"""T051/T055/T056/T057/T119: per-route create/poll/fetch/cancel offline contracts."""

import base64
import json
from typing import Any

import httpx
import pytest

from imagen_cli.domain.models import ProviderProfile
from imagen_cli.providers.automatic1111 import Automatic1111Adapter
from imagen_cli.providers.base import ProviderFailure, RemoteObservation, ValidatedRequest
from imagen_cli.providers.bfl import BFLAdapter
from imagen_cli.providers.comfyui import ComfyUIAdapter
from imagen_cli.providers.google_cloud import VertexGeminiAdapter, VertexImagenAdapter
from imagen_cli.providers.model_platforms import (
    FalAdapter,
    ModelDeclaration,
    ReplicateAdapter,
)
from imagen_cli.providers.openai_compatible import OpenAICompatibleAdapter
from imagen_cli.providers.openrouter import OpenRouterAdapter
from imagen_cli.providers.stability import StabilityAdapter


def profile(
    provider: str,
    family: str,
    base_url: str,
    model: str,
    *,
    trust_policy: dict[str, Any] | None = None,
) -> ProviderProfile:
    return ProviderProfile(
        id=f"{provider}-local", name="offline", provider=provider, family=family,
        base_url=base_url, model_default=model, credential_ref="env:FIXTURE",
        trust_policy=trust_policy or {}, revision=1,
    )


def request(
    target: ProviderProfile,
    *,
    operation: str = "generate",
    inputs: tuple[bytes, ...] = (),
    mask: bytes | None = None,
    parameters: dict[str, Any] | None = None,
    model: str | None = None,
    authorized: bool = True,
) -> ValidatedRequest:
    return ValidatedRequest(
        plan_id="pln_" + "1" * 32, job_id="job_" + "2" * 32,
        profile_identity=target.id, profile_revision=target.revision,
        operation=operation, model=model or target.model_default,
        model_revision="2026-10", prompt="a red kite",
        normalized_parameters=parameters or {},
        capability_digest="a" * 64, authorized=authorized,
        dispatch_intent_recorded=True, inputs=inputs, mask=mask,
    )


def encoded(png: bytes) -> str:
    return base64.b64encode(png).decode("ascii")


def test_bfl_submit_poll_fetch_and_documented_absence_of_cancel(png_bytes: bytes) -> None:
    calls: list[tuple[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, str(req.url)))
        if req.method == "POST":
            assert req.headers["x-key"] == "fixture-secret"
            body = json.loads(req.content)
            assert body["prompt"] == "a red kite"
            assert body["width"] == 1024 and body["height"] == 1024
            return httpx.Response(200, json={
                "id": "task-1",
                "polling_url": "https://api.bfl.ai/v1/get_result?id=task-1",
            })
        if req.url.host == "api.bfl.ai":
            return httpx.Response(200, json={
                "id": "task-1", "status": "Ready",
                "result": {"sample": "https://delivery.bfl.ai/result.png"},
            })
        return httpx.Response(200, content=png_bytes)

    target = profile("bfl", "flux", "https://api.bfl.ai", "flux-pro-1.1")
    adapter = BFLAdapter(
        target, transport=httpx.MockTransport(handler),
        resolve_credential=lambda ref: "fixture-secret",
    )
    handle = adapter.create(request(target, parameters={"size": "1024x1024"}))
    observation = adapter.poll(handle)
    assert observation.state == "succeeded"
    result = adapter.fetch(observation)
    assert result.artifacts[0].content == png_bytes
    assert adapter.cancel(handle).state == "unsupported"
    assert len(calls) == 3
    assert all(call[1] != "https://api.bfl.ai" or call[0] != "DELETE" for call in calls)


def test_bfl_unauthorized_never_sends_and_redirect_is_not_followed(png_bytes: bytes) -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.method)
        return httpx.Response(307, headers={"location": "https://evil.example.test/"})

    target = profile("bfl", "flux", "https://api.bfl.ai", "flux-pro-1.1")
    adapter = BFLAdapter(
        target, transport=httpx.MockTransport(handler),
        resolve_credential=lambda ref: "fixture-secret",
    )
    with pytest.raises(ProviderFailure, match="approval_required"):
        adapter.create(request(target, authorized=False))
    assert calls == []
    with pytest.raises(ProviderFailure) as failure:
        adapter.create(request(target))
    assert failure.value.type == "outcome_unknown"
    assert calls == ["POST"]


def test_stability_generate_and_safety_refusal_use_json_multipart(png_bytes: bytes) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "Bearer fixture-secret"
        assert req.headers["accept"] == "application/json"
        assert b'name="prompt"' in req.content
        return httpx.Response(200, json={
            "image": encoded(png_bytes), "seed": 42, "finish_reason": "SUCCESS",
        })

    target = profile("stability", "stable_image", "https://api.stability.ai", "core")
    adapter = StabilityAdapter(
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

    def filtered(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "image": encoded(png_bytes), "finish_reason": "CONTENT_FILTERED",
        })

    refusing = StabilityAdapter(
        target, transport=httpx.MockTransport(filtered),
        resolve_credential=lambda ref: "fixture-secret",
    )
    with pytest.raises(ProviderFailure) as failure:
        refusing.create(request(target))
    assert failure.value.type == "safety_refusal"


def test_fal_queue_declaration_gate_poll_fetch_and_cancel(png_bytes: bytes) -> None:
    calls: list[tuple[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path))
        if req.method == "POST":
            assert req.headers["authorization"] == "Key fixture-secret"
            assert json.loads(req.content)["prompt"] == "a red kite"
            assert json.loads(req.content)["num_images"] == 1
            return httpx.Response(200, json={
                "request_id": "req-1",
                "status_url": "https://queue.fal.run/fal-ai/flux/requests/req-1/status",
                "response_url": "https://queue.fal.run/fal-ai/flux/requests/req-1",
                "cancel_url": "https://queue.fal.run/fal-ai/flux/requests/req-1/cancel",
            })
        if req.url.path.endswith("/status"):
            return httpx.Response(200, json={
                "status": "COMPLETED",
                "response_url": "https://queue.fal.run/fal-ai/flux/requests/req-1",
            })
        if req.method == "PUT":
            return httpx.Response(202, json={"status": "CANCELLATION_REQUESTED"})
        if req.url.host == "v3.fal.media":
            return httpx.Response(200, content=png_bytes)
        return httpx.Response(200, json={"images": [{"url": "https://v3.fal.media/x.png"}]})

    target = profile("fal", "model_api", "https://queue.fal.run", "fal-ai/flux/schnell")
    declarations = {
        "fal-ai/flux/schnell": ModelDeclaration(
            operations=frozenset({"generate"}),
            allowed_parameters=frozenset({"count"}),
            parameter_fields={"count": "num_images"},
            output_path=("images",),
        ),
    }
    adapter = FalAdapter(
        target, transport=httpx.MockTransport(handler), declarations=declarations,
        resolve_credential=lambda ref: "fixture-secret",
    )
    handle = adapter.create(request(target, parameters={"count": 1}))
    assert handle.remote_id == "req-1"
    observation = adapter.poll(handle)
    assert observation.state == "succeeded"
    assert adapter.fetch(observation).artifacts[0].content == png_bytes
    assert adapter.cancel(handle).state == "requested"
    assert len(calls) == 5

    undeclared = FalAdapter(
        target, transport=httpx.MockTransport(lambda req: pytest.fail("no request")),
        resolve_credential=lambda ref: "fixture-secret",
    )
    with pytest.raises(ProviderFailure, match="unsupported"):
        undeclared.create(request(target))


def test_replicate_resolves_model_or_version_and_cancels() -> None:
    urls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        urls.append(f"{req.method} {req.url.path}")
        if req.method == "POST" and req.url.path.endswith("/cancel"):
            return httpx.Response(200, json={"status": "canceled"})
        if req.method == "POST":
            return httpx.Response(200, json={
                "id": "pred-1", "status": "starting",
                "urls": {
                    "get": "https://api.replicate.com/v1/predictions/pred-1",
                    "cancel": "https://api.replicate.com/v1/predictions/pred-1/cancel",
                },
            })
        return httpx.Response(200, json={
            "id": "pred-1", "status": "succeeded",
            "output": ["https://replicate.delivery/x.png"],
        })

    target = profile(
        "replicate", "predictions", "https://api.replicate.com",
        "black-forest-labs/flux-schnell",
    )
    declarations = {
        "black-forest-labs/flux-schnell": ModelDeclaration(
            operations=frozenset({"generate"}),
            allowed_parameters=frozenset(),
            output_path=("output",),
        ),
    }
    adapter = ReplicateAdapter(
        target, transport=httpx.MockTransport(handler), declarations=declarations,
        resolve_credential=lambda ref: "fixture-secret",
    )
    handle = adapter.create(request(target))
    assert urls[0] == (
        "POST /v1/models/black-forest-labs/flux-schnell/predictions"
    )
    observation = adapter.poll(handle)
    assert observation.state == "succeeded"
    assert adapter.cancel(handle).state == "canceled"

    version = "a" * 64
    pinned = request(target, model=f"black-forest-labs/flux-schnell:{version}")
    adapter.create(pinned)
    assert urls[-1] == "POST /v1/predictions"

    with pytest.raises(ProviderFailure, match="validation_error"):
        adapter.create(request(target, model="https://evil.example.test/model"))


def test_replicate_requires_a_declaration_before_any_request() -> None:
    target = profile(
        "replicate", "predictions", "https://api.replicate.com",
        "black-forest-labs/flux-schnell",
    )
    adapter = ReplicateAdapter(
        target, transport=httpx.MockTransport(lambda req: pytest.fail("no request")),
        resolve_credential=lambda ref: "fixture-secret",
    )
    with pytest.raises(ProviderFailure, match="unsupported"):
        adapter.create(request(target))


def test_openrouter_image_api_is_synchronous_and_never_borrows_vendor_capabilities(
    png_bytes: bytes,
) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v1/images"
        assert req.headers["authorization"] == "Bearer fixture-secret"
        body = json.loads(req.content)
        assert body["model"] == "google/gemini-2.5-flash-image"
        assert body["n"] == 1
        return httpx.Response(200, json={
            "data": [{"b64_json": encoded(png_bytes), "media_type": "image/png"}],
            "usage": {"cost": 0.01},
        })

    target = profile(
        "openrouter", "images", "https://openrouter.ai",
        "google/gemini-2.5-flash-image",
    )
    declarations = {
        "google/gemini-2.5-flash-image": ModelDeclaration(
            operations=frozenset({"generate"}),
            allowed_parameters=frozenset({"count", "aspect_ratio"}),
        ),
    }
    adapter = OpenRouterAdapter(
        target, transport=httpx.MockTransport(handler), declarations=declarations,
        resolve_credential=lambda ref: "fixture-secret",
    )
    result = adapter.create(request(target, parameters={"count": 1}))
    assert result.artifacts[0].content == png_bytes
    assert adapter.cancel(object()).state == "unsupported"
    with pytest.raises(ProviderFailure, match="unsupported"):
        adapter.poll(object())
    with pytest.raises(ProviderFailure, match="unsupported"):
        adapter.fetch(RemoteObservation("succeeded"))
    with pytest.raises(ProviderFailure, match="unsupported"):
        adapter.create(request(target, operation="edit", inputs=(png_bytes,)))


def test_comfyui_explicit_workflow_prompt_history_view_and_pending_cancel(
    png_bytes: bytes,
) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/prompt":
            body = json.loads(req.content)
            assert body["prompt"]["6"]["inputs"]["text"] == "a red kite"
            return httpx.Response(200, json={
                "prompt_id": "p1", "number": 1, "node_errors": {},
            })
        if req.url.path == "/history/p1":
            return httpx.Response(200, json={"p1": {
                "status": {"status_str": "success", "completed": True},
                "outputs": {"9": {"images": [
                    {"filename": "out.png", "subfolder": "", "type": "output"},
                ]}},
            }})
        if req.url.path == "/view":
            assert req.url.params["filename"] == "out.png"
            return httpx.Response(200, content=png_bytes)
        assert req.url.path == "/queue"
        return httpx.Response(200, json={})

    target = profile("comfyui", "workflow", "https://comfy.example.test", "local-workflow")
    adapter = ComfyUIAdapter(
        target, transport=httpx.MockTransport(handler),
        resolve_credential=lambda ref: "fixture-secret",
        workflow={"6": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}}},
        prompt_node="6",
    )
    handle = adapter.create(request(target))
    observation = adapter.poll(handle)
    assert observation.state == "succeeded"
    assert adapter.fetch(observation).artifacts[0].content == png_bytes
    assert adapter.cancel(handle).state == "requested"


def test_vertex_imagen_predict_and_gemini_generate_content(png_bytes: bytes) -> None:
    paths: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        paths.append(req.url.path)
        assert req.headers["authorization"] == "Bearer adc-token"
        if req.url.path.endswith(":predict"):
            assert "/projects/my-project-123/locations/us-central1/" in req.url.path
            return httpx.Response(200, json={"predictions": [{
                "bytesBase64Encoded": encoded(png_bytes), "mimeType": "image/png",
            }]})
        return httpx.Response(200, json={"candidates": [{
            "finishReason": "STOP",
            "content": {"parts": [{"inlineData": {
                "mimeType": "image/png", "data": encoded(png_bytes),
            }}]},
        }]})

    trust = {"project_id": "my-project-123", "location": "us-central1"}
    imagen = profile(
        "vertex", "imagen", "https://us-central1-aiplatform.googleapis.com",
        "imagen-4.0-generate-001", trust_policy=trust,
    )
    adapter = VertexImagenAdapter(
        imagen, transport=httpx.MockTransport(handler),
        resolve_credential=lambda ref: "adc-token",
    )
    assert adapter.create(request(imagen)).artifacts[0].content == png_bytes
    assert adapter.cancel(object()).state == "unsupported"
    with pytest.raises(ProviderFailure, match="unsupported"):
        adapter.poll(object())
    with pytest.raises(ProviderFailure, match="unsupported"):
        adapter.fetch(RemoteObservation("succeeded"))
    with pytest.raises(ProviderFailure, match="unsupported"):
        adapter.create(request(imagen, operation="edit", inputs=(png_bytes,)))

    gemini = profile(
        "vertex", "gemini", "https://us-central1-aiplatform.googleapis.com",
        "gemini-2.5-flash-image", trust_policy=trust,
    )
    gemini_adapter = VertexGeminiAdapter(
        gemini, transport=httpx.MockTransport(handler),
        resolve_credential=lambda ref: "adc-token",
    )
    result = gemini_adapter.create(request(gemini, operation="edit", inputs=(png_bytes,)))
    assert result.artifacts[0].content == png_bytes
    assert any(path.endswith(":predict") for path in paths)
    assert any(path.endswith(":generateContent") for path in paths)


def test_vertex_requires_explicit_project_location_and_trusted_host(png_bytes: bytes) -> None:
    untrusted = profile(
        "vertex", "imagen", "https://api.example.test", "imagen-4.0-generate-001",
        trust_policy={"project_id": "my-project-123", "location": "us-central1"},
    )
    adapter = VertexImagenAdapter(
        untrusted, transport=httpx.MockTransport(lambda req: pytest.fail("no request")),
        resolve_credential=lambda ref: "adc-token",
    )
    with pytest.raises(ProviderFailure, match="validation"):
        adapter.create(request(untrusted))


def test_openai_compatible_declaration_gate_and_inline_only(png_bytes: bytes) -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        return httpx.Response(200, json={
            "data": [{"b64_json": encoded(png_bytes)}], "usage": {"total_tokens": 1},
        })

    target = profile(
        "openai-compatible", "images", "https://compatible.example.test/v1",
        "declared-image-model",
    )
    declarations = {
        "declared-image-model": ModelDeclaration(
            operations=frozenset({"generate", "edit"}),
            allowed_parameters=frozenset({"count", "size"}),
        ),
    }
    adapter = OpenAICompatibleAdapter(
        target, transport=httpx.MockTransport(handler), declarations=declarations,
        resolve_credential=lambda ref: "fixture-secret",
    )
    result = adapter.create(request(target, parameters={"count": 1, "size": "1024x1024"}))
    assert result.artifacts[0].content == png_bytes
    assert calls == ["/v1/images/generations"]
    assert adapter.cancel(object()).state == "unsupported"
    with pytest.raises(ProviderFailure, match="unsupported"):
        adapter.poll(object())
    with pytest.raises(ProviderFailure, match="unsupported"):
        adapter.fetch(RemoteObservation("succeeded"))

    undeclared = OpenAICompatibleAdapter(
        target, transport=httpx.MockTransport(lambda req: pytest.fail("no request")),
        declarations={}, resolve_credential=lambda ref: "fixture-secret",
    )
    with pytest.raises(ProviderFailure, match="unsupported"):
        undeclared.create(request(target))


def test_openai_compatible_rejects_result_urls_instead_of_fetching_them() -> None:
    target = profile(
        "openai-compatible", "images", "https://compatible.example.test/v1", "declared-image-model",
    )
    adapter = OpenAICompatibleAdapter(
        target,
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"data": [{"url": "https://evil.test/x.png"}]})
        ),
        declarations={
            "declared-image-model": ModelDeclaration(
                operations=frozenset({"generate"}),
                allowed_parameters=frozenset({"count"}),
            ),
        },
        resolve_credential=lambda ref: "fixture-secret",
    )
    with pytest.raises(ProviderFailure) as failure:
        adapter.create(request(target))
    assert failure.value.type == "provider_error"


def test_automatic1111_named_self_hosted_route_generates_and_interrupts(png_bytes: bytes) -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        if req.url.path == "/sdapi/v1/txt2img":
            body = json.loads(req.content)
            assert body["prompt"] == "a red kite"
            assert body["width"] == 512 and body["height"] == 512
            assert body["batch_size"] == 2
            return httpx.Response(200, json={"images": [encoded(png_bytes)] * 2, "info": "{}"})
        return httpx.Response(200, json={})

    target = profile(
        "automatic1111", "sdapi", "https://webui.example.test", "webui-checkpoint",
    )
    adapter = Automatic1111Adapter(
        target, transport=httpx.MockTransport(handler),
        resolve_credential=lambda ref: "fixture-secret",
    )
    result = adapter.create(request(target, parameters={
        "count": 2, "size": "512x512", "seed": 7,
    }))
    assert result.actual_count == 2
    assert adapter.cancel().state == "requested"
    assert calls == ["/sdapi/v1/txt2img", "/sdapi/v1/interrupt"]
    with pytest.raises(ProviderFailure, match="unsupported"):
        adapter.poll(object())
    with pytest.raises(ProviderFailure, match="unsupported"):
        adapter.fetch(RemoteObservation("succeeded"))
