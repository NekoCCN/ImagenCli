"""Canonical offline-reviewed capability records for the implemented M2 routes.

These are declarations backed by offline contract tests only. No record is T:
live evidence is intentionally empty until a separately authorized live run.
"""

from typing import Any

from imagen_cli.domain.models import CapabilityRecord

from . import records as records_module

__all__ = ["CANONICAL_RECORDS", "offline_source"]

_CHECKED_AT = "2026-10-24T00:00:00Z"
_TEST = "tests/contract/test_provider_adapters.py"


def offline_source(url: str, locator: str) -> dict[str, str]:
    return {"url": url, "date": "2026-10-24", "locator": locator}


def _record(
    identity: str,
    provider: str,
    family: str,
    model: str,
    revision: str,
    operation: str,
    *,
    url: str,
    locator: str,
    parameters: dict[str, Any],
    transport: str = "sync",
    continuation: str = "none",
    cancel: dict[str, Any] | None = None,
    semantic: dict[str, Any] | None = None,
) -> CapabilityRecord:
    return records_module.build({
        "id": identity,
        "provider": provider,
        "family": family,
        "model": model,
        "model_revision": revision,
        "operation": operation,
        "support_status": "I",
        "parameter_schema": {
            "type": "object",
            "properties": parameters,
            "additionalProperties": False,
        },
        "semantic_mapping": semantic or {
            "prompt": "prompt",
            "inputs": "explicit_images" if operation == "edit" else "none",
        },
        "input_limits": {"max_images": 4, "max_image_bytes": 67108864},
        "transport_kind": transport,
        "continuation_strategy": continuation,
        "idempotency_contract": {"scope": "none"},
        "cancel_contract": cancel or {"supported": False, "kind": "sync"},
        "sources": [offline_source(url, locator)],
        "checked_at": _CHECKED_AT,
        "offline_evidence": [{
            "kind": "offline_contract", "test": _TEST, "verified_at": _CHECKED_AT,
        }],
        "live_evidence": [],
    })


_SIMPLE_PARAMETERS: dict[str, Any] = {
    "count": {"type": "integer", "minimum": 1},
    "size": {"type": "string"},
    "seed": {"type": "integer", "minimum": 0},
    "output_format": {"enum": ["png", "jpeg", "webp"]},
}

CANONICAL_RECORDS: tuple[CapabilityRecord, ...] = (
    _record(
        "openai-images-generate", "openai", "images", "gpt-image-1", "2026-10", "generate",
        url="https://platform.openai.com/docs/guides/image-generation",
        locator="Images API generate", parameters=_SIMPLE_PARAMETERS,
    ),
    _record(
        "openai-images-edit", "openai", "images", "gpt-image-1", "2026-10", "edit",
        url="https://platform.openai.com/docs/guides/image-generation",
        locator="Images API edit", parameters=_SIMPLE_PARAMETERS,
        continuation="explicit_images",
    ),
    _record(
        "openai-responses-generate", "openai", "responses", "gpt-image-1", "2026-10",
        "generate",
        url="https://developers.openai.com/api/docs/guides/tools-image-generation",
        locator="image_generation tool", parameters=_SIMPLE_PARAMETERS,
    ),
    _record(
        "openai-responses-edit", "openai", "responses", "gpt-image-1", "2026-10", "edit",
        url="https://developers.openai.com/api/docs/guides/tools-image-generation",
        locator="input_image content", parameters=_SIMPLE_PARAMETERS,
        continuation="explicit_images",
    ),
    _record(
        "gemini-generate", "gemini", "generate_content", "gemini-2.5-flash-image", "2026-10",
        "generate", url="https://ai.google.dev/gemini-api/docs/image-generation",
        locator="generateContent image response",
        parameters={"count": {"const": 1}, "aspect_ratio": {"type": "string"}},
        continuation="native_parts",
    ),
    _record(
        "gemini-edit", "gemini", "generate_content", "gemini-2.5-flash-image", "2026-10", "edit",
        url="https://ai.google.dev/gemini-api/docs/image-generation",
        locator="inlineData input parts",
        parameters={"count": {"const": 1}, "aspect_ratio": {"type": "string"}},
        continuation="native_parts",
    ),
    _record(
        "vertex-gemini-generate", "vertex", "gemini", "gemini-2.5-flash-image", "2026-10",
        "generate", url="https://cloud.google.com/vertex-ai/generative-ai/docs/image/overview",
        locator="Vertex generateContent", parameters={"count": {"const": 1}},
    ),
    _record(
        "vertex-gemini-edit", "vertex", "gemini", "gemini-2.5-flash-image", "2026-10", "edit",
        url="https://cloud.google.com/vertex-ai/generative-ai/docs/image/overview",
        locator="Vertex inlineData parts", parameters={"count": {"const": 1}},
        continuation="explicit_images",
    ),
    _record(
        "vertex-imagen-generate", "vertex", "imagen", "imagen-4.0-generate-001", "2026-10",
        "generate",
        url="https://cloud.google.com/vertex-ai/generative-ai/docs/image/generate-images",
        locator="endpoints.predict",
        parameters={"count": {"type": "integer", "minimum": 1, "maximum": 4}, **_SIMPLE_PARAMETERS},
    ),
    _record(
        "bfl-flux-generate", "bfl", "flux", "flux-pro-1.1", "2026-10", "generate",
        url="https://docs.bfl.ai/quick_start/generating_images", locator="submit and polling_url",
        parameters=_SIMPLE_PARAMETERS, transport="remote_async",
    ),
    _record(
        "bfl-kontext-edit", "bfl", "flux", "flux-kontext-pro", "2026-10", "edit",
        url="https://docs.bfl.ai/", locator="flux-kontext-pro input_image",
        parameters=_SIMPLE_PARAMETERS, transport="remote_async",
        continuation="explicit_images",
    ),
    _record(
        "bfl-fill-edit", "bfl", "flux", "flux-pro-1.0-fill", "2026-10", "edit",
        url="https://docs.bfl.ai/", locator="flux-pro-1.0-fill image and mask",
        parameters=_SIMPLE_PARAMETERS, transport="remote_async",
        continuation="explicit_images",
    ),
    _record(
        "stability-core-generate", "stability", "stable_image", "core", "2026-10", "generate",
        url="https://platform.stability.ai/docs/api-reference",
        locator="POST /v2beta/stable-image/generate/core",
        parameters={"count": {"const": 1}, "aspect_ratio": {"type": "string"},
                    "seed": {"type": "integer"}, "output_format": {"type": "string"}},
    ),
    _record(
        "stability-inpaint-edit", "stability", "stable_image", "inpaint", "2026-10", "edit",
        url="https://platform.stability.ai/docs/api-reference",
        locator="POST /v2beta/stable-image/edit/inpaint",
        parameters={"count": {"const": 1}, "seed": {"type": "integer"},
                    "output_format": {"type": "string"}},
        continuation="explicit_images",
    ),
    _record(
        "fal-flux-generate", "fal", "model_api", "fal-ai/flux/schnell", "declared", "generate",
        url="https://fal.ai/docs/documentation/model-apis/inference/queue",
        locator="queue submit/status/result/cancel", parameters=_SIMPLE_PARAMETERS,
        transport="remote_async", cancel={"supported": True, "kind": "queue_cancel"},
    ),
    _record(
        "replicate-flux-generate", "replicate", "predictions",
        "black-forest-labs/flux-schnell", "declared", "generate",
        url="https://replicate.com/docs/reference/http",
        locator="predictions.create/get/cancel", parameters=_SIMPLE_PARAMETERS,
        transport="remote_async", cancel={"supported": True, "kind": "prediction_cancel"},
    ),
    _record(
        "openrouter-image-generate", "openrouter", "images",
        "google/gemini-2.5-flash-image", "2026-10", "generate",
        url="https://openrouter.ai/docs/guides/overview/multimodal/image-generation",
        locator="POST /api/v1/images", parameters=_SIMPLE_PARAMETERS,
    ),
    _record(
        "comfyui-workflow-generate", "comfyui", "workflow", "local-workflow", "declared",
        "generate", url="https://docs.comfy.org/development/comfyui-server/comms_routes",
        locator="POST /prompt, GET /history, GET /view",
        parameters={"count": {"const": 1}}, transport="remote_async",
        cancel={"supported": True, "kind": "pending_queue_delete"},
    ),
    _record(
        "automatic1111-generate", "automatic1111", "sdapi", "webui-checkpoint", "declared",
        "generate",
        url="https://github.com/AUTOMATIC1111/stable-diffusion-webui/wiki/API",
        locator="POST /sdapi/v1/txt2img", parameters=_SIMPLE_PARAMETERS,
        cancel={"supported": True, "kind": "interrupt"},
    ),
    _record(
        "automatic1111-edit", "automatic1111", "sdapi", "webui-checkpoint", "declared", "edit",
        url="https://github.com/AUTOMATIC1111/stable-diffusion-webui/wiki/API",
        locator="POST /sdapi/v1/img2img init_images and mask",
        parameters=_SIMPLE_PARAMETERS, continuation="explicit_images",
        cancel={"supported": True, "kind": "interrupt"},
    ),
)
