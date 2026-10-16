"""Route x dimension coverage matrix; evidence gates every status transition."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

__all__ = [
    "DIMENSIONS", "DIMENSION_KEYS", "ROUTES", "CoverageCell", "CoverageRoute",
    "matrix", "transition",
]

# The 18 reviewed dimensions in the order required by FR-010.
DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("generate", "G text-to-image"),
    ("image_to_image", "I image-to-image"),
    ("edit", "E instruction edit"),
    ("mask", "M mask inpaint"),
    ("outpaint", "O outpainting"),
    ("multi_reference", "R multiple references"),
    ("upscale", "U upscale"),
    ("background", "B background/transparency"),
    ("size", "S size/aspect ratio"),
    ("count", "C count"),
    ("seed", "D seed"),
    ("quality", "Q quality"),
    ("format", "F format/compression"),
    ("safety", "A safety refusal"),
    ("stream_preview", "P streaming preview"),
    ("batch", "T batching"),
    ("remote_cancel", "X remote cancellation"),
    ("native_context", "N native multi-turn"),
)
_KEYS = tuple(key for key, _ in DIMENSIONS)
_STATUSES = {"T", "I", "D", "N", "U"}


@dataclass(frozen=True)
class CoverageCell:
    status: str
    evidence: str


@dataclass(frozen=True)
class CoverageRoute:
    name: str
    source: str
    source_date: str
    cells: Mapping[str, CoverageCell]

    def __post_init__(self) -> None:
        if set(self.cells) != set(_KEYS):
            raise ValueError("coverage route must state all 18 dimensions")
        for cell in self.cells.values():
            if cell.status not in _STATUSES or not cell.evidence.strip():
                raise ValueError("coverage cell requires a known status and evidence")


def _route(
    name: str, source: str, cells: Mapping[str, tuple[str, str]],
) -> CoverageRoute:
    return CoverageRoute(
        name, source, "2026-10-24",
        MappingProxyType({
            key: CoverageCell(*cells.get(key, ("U", "not reviewed in this milestone")))
            for key in _KEYS
        }),
    )


# No T cell exists: no live evidence was collected in this milestone. I is offline-only.
ROUTES: tuple[CoverageRoute, ...] = (
    _route("OpenAI Images", "https://platform.openai.com/docs/guides/image-generation", {
        "generate": ("I", "offline: T028 adapter fixture"),
        "image_to_image": ("I", "offline: edit multipart fixture"),
        "edit": ("I", "offline: edit multipart fixture"),
        "mask": ("D", "documented images/edits mask field; no mask fixture"),
        "multi_reference": ("D", "documented image[] field"),
        "background": ("D", "documented background parameter"),
        "size": ("D", "documented size values"),
        "count": ("D", "documented n field"),
        "quality": ("D", "documented quality values"),
        "seed": ("U", "not reviewed"),
        "format": ("D", "documented output_format/compression"),
        "safety": ("D", "documented content_policy_violation code"),
        "stream_preview": ("N", "Images API response is a single JSON body"),
        "batch": ("D", "documented n>1 in one request"),
        "remote_cancel": ("N", "no cancellation route documented"),
        "native_context": ("N", "stateless image endpoint"),
    }),
    _route(
        "OpenAI Responses image tool",
        "https://developers.openai.com/api/docs/guides/tools-image-generation",
        {
            "generate": ("I", "offline: responses fixture"),
            "image_to_image": ("I", "offline: input_image content"),
            "edit": ("I", "offline: input_image content"),
            "background": ("D", "documented image tool parameter"),
            "size": ("D", "documented image tool parameter"),
            "quality": ("D", "documented image tool parameter"),
            "format": ("D", "documented image tool parameter"),
            "safety": ("D", "response failure status is reported"),
            "stream_preview": ("D", "documented partial_images; not implemented"),
            "count": ("N", "one image_generation_call per request in this adapter"),
            "remote_cancel": ("N", "no cancel route for a stateless Responses call"),
            "native_context": ("N", "adapter deliberately does not send previous_response_id"),
        },
    ),
    _route("OpenAI-compatible", "https://platform.openai.com/docs/api-reference/images", {
        "generate": ("I", "offline: declared model fixture"),
        "edit": ("I", "offline: declared edit fixture"),
        "safety": ("D", "content_policy_violation shape when present"),
        "stream_preview": ("N", "shape has no stream parameter"),
        "remote_cancel": ("N", "shape has no cancel route"),
        "native_context": ("N", "stateless shape"),
    }),
    _route("Gemini Developer API", "https://ai.google.dev/gemini-api/docs/image-generation", {
        "generate": ("I", "offline: T029 native context fixture"),
        "image_to_image": ("I", "offline: inlineData inputs"),
        "edit": ("I", "offline: T029 edit fixture"),
        "mask": ("N", "generateContent images carry no mask field"),
        "multi_reference": ("D", "documented multiple inlineData parts"),
        "size": ("D", "documented aspectRatio"),
        "count": ("N", "adapter requires count=1"),
        "safety": ("D", "documented finishReason/blockReason"),
        "stream_preview": ("N", "adapter requests one complete response"),
        "batch": ("N", "one candidate path per request"),
        "remote_cancel": ("N", "generateContent has no cancel route"),
        "native_context": ("I", "offline: private native parts restoration"),
    }),
    _route(
        "Vertex AI Gemini",
        "https://cloud.google.com/vertex-ai/generative-ai/docs/image/overview",
        {
            "generate": ("I", "offline: Vertex generateContent fixture"),
            "image_to_image": ("I", "offline: Vertex inlineData input"),
            "edit": ("I", "offline: Vertex edit fixture"),
            "mask": ("N", "generateContent images carry no mask field"),
            "size": ("D", "documented imageConfig aspectRatio"),
            "safety": ("D", "documented finishReason"),
            "remote_cancel": ("N", "generateContent has no cancel route"),
            "native_context": ("N", "adapter rejects native context"),
        },
    ),
    _route(
        "Google Imagen",
        "https://cloud.google.com/vertex-ai/generative-ai/docs/image/generate-images",
        {
            "generate": ("I", "offline: endpoints.predict fixture"),
            "size": ("D", "documented aspectRatio"),
            "count": ("D", "documented sampleCount 1-4"),
            "seed": ("D", "documented seed"),
            "format": ("D", "documented outputOptions.mimeType"),
            "safety": ("D", "documented raiFilteredReason"),
            "edit": ("U", "editing route not verified or implemented"),
            "stream_preview": ("N", "predict returns one complete response"),
            "remote_cancel": ("N", "predict has no cancel route"),
            "native_context": ("N", "stateless predict"),
        },
    ),
    _route("BFL / FLUX", "https://docs.bfl.ai/", {
        "generate": ("I", "offline: flux-pro-1.1 submit/poll/fetch"),
        "image_to_image": ("I", "offline: flux-kontext-pro input_image"),
        "edit": ("I", "offline: flux-kontext-pro input_image"),
        "mask": ("I", "offline: flux-pro-1.0-fill image+mask"),
        "outpaint": ("D", "documented flux-pro-1.0-expand route; not implemented"),
        "size": ("I", "offline: width/height validation"),
        "count": ("N", "one task produces one sample per request"),
        "seed": ("I", "offline: seed forwarded"),
        "format": ("I", "offline: output_format forwarded"),
        "safety": ("D", "documented moderated statuses"),
        "stream_preview": ("N", "documented task polling, not partial images"),
        "batch": ("N", "one task per request"),
        "remote_cancel": ("N", "no cancellation route in the published API"),
        "native_context": ("N", "stateless task API"),
    }),
    _route("Stability AI", "https://platform.stability.ai/docs/api-reference", {
        "generate": ("I", "offline: generate/core fixture"),
        "image_to_image": ("I", "offline: edit/inpaint image"),
        "edit": ("I", "offline: edit/inpaint fixture"),
        "mask": ("I", "offline: edit/inpaint mask"),
        "outpaint": ("I", "offline: edit/outpaint route"),
        "upscale": ("D", "documented upscale services; not implemented"),
        "background": ("D", "documented remove-background service"),
        "size": ("I", "offline: aspect_ratio forwarded"),
        "count": ("N", "adapter requires count=1"),
        "seed": ("I", "offline: seed forwarded"),
        "format": ("I", "offline: output_format forwarded"),
        "safety": ("D", "documented CONTENT_FILTERED finish reason"),
        "stream_preview": ("N", "single response"),
        "batch": ("N", "one image per request on these routes"),
        "remote_cancel": ("N", "no cancel route for synchronous image services"),
        "native_context": ("N", "stateless service"),
    }),
    _route("fal.ai", "https://fal.ai/docs/documentation/model-apis/inference/queue", {
        "generate": ("I", "offline: declared queue fixture"),
        "image_to_image": ("I", "offline: declared image field"),
        "edit": ("I", "offline: declared edit fixture"),
        "multi_reference": ("I", "offline: declared images field"),
        "mask": ("D", "declaration may map a mask field"),
        "size": ("D", "model-declared parameters"),
        "count": ("D", "model-declared num_images parameter"),
        "seed": ("D", "model-declared parameter"),
        "quality": ("D", "model-declared parameter"),
        "format": ("D", "model-declared parameter"),
        "stream_preview": ("N", "queue exposes status/logs, not image previews"),
        "batch": ("D", "queue accepts independent requests"),
        "remote_cancel": ("I", "offline: PUT cancel route"),
        "native_context": ("N", "stateless queue"),
    }),
    _route("Replicate", "https://replicate.com/docs/reference/http", {
        "generate": ("I", "offline: model-route prediction fixture"),
        "image_to_image": ("I", "offline: declared image field"),
        "edit": ("I", "offline: declared edit fixture"),
        "multi_reference": ("I", "offline: declared images field"),
        "mask": ("D", "declaration may map a mask field"),
        "size": ("D", "model schema parameter"),
        "count": ("D", "model schema parameter"),
        "seed": ("D", "model schema parameter"),
        "quality": ("D", "model schema parameter"),
        "format": ("D", "model schema parameter"),
        "stream_preview": ("D", "documented output streaming; not implemented"),
        "batch": ("D", "independent predictions"),
        "remote_cancel": ("I", "offline: predictions/{id}/cancel"),
        "native_context": ("N", "stateless prediction"),
    }),
    _route(
        "OpenRouter",
        "https://openrouter.ai/docs/guides/overview/multimodal/image-generation",
        {
            "generate": ("I", "offline: /api/v1/images fixture"),
            "image_to_image": ("D", "documented reference images; not implemented"),
            "multi_reference": ("D", "documented reference images; not implemented"),
            "background": ("D", "documented background parameter"),
            "size": ("D", "documented size/aspect_ratio/resolution"),
            "count": ("D", "documented n upper bound"),
            "seed": ("D", "documented per-model parameter"),
            "quality": ("D", "documented quality parameter"),
            "format": ("D", "documented output_format/compression"),
            "safety": ("D", "error response reports provider failure"),
            "stream_preview": ("D", "documented SSE partial images; not implemented"),
            "remote_cancel": ("N", "no cancel route; billing is all-or-nothing"),
            "native_context": ("N", "stateless image API"),
        },
    ),
    _route("ComfyUI", "https://docs.comfy.org/development/comfyui-server/comms_routes", {
        "generate": ("I", "offline: explicit workflow prompt/history/view"),
        "remote_cancel": ("I", "offline: pending queue delete"),
        "mask": ("N", "adapter rejects input/mask uploads"),
        "image_to_image": ("N", "adapter rejects input images"),
        "stream_preview": ("D", "documented websocket progress; not implemented"),
        "batch": ("D", "queue accepts independent prompts"),
        "native_context": ("N", "workflow queue is stateless across prompts"),
    }),
    _route(
        "AUTOMATIC1111 Stable Diffusion WebUI API",
        "https://github.com/AUTOMATIC1111/stable-diffusion-webui/wiki/API",
        {
            "generate": ("I", "offline: /sdapi/v1/txt2img fixture"),
            "image_to_image": ("I", "offline: /sdapi/v1/img2img init_images"),
            "edit": ("I", "offline: /sdapi/v1/img2img init_images"),
            "mask": ("I", "offline: img2img mask field"),
            "upscale": ("D", "documented extras API; not implemented"),
            "size": ("I", "offline: width/height mapping"),
            "count": ("I", "offline: batch_size mapping"),
            "seed": ("I", "offline: seed forwarded"),
            "quality": ("D", "documented steps/cfg_scale; forwarded"),
            "format": ("N", "responses are base64 PNG in this adapter"),
            "stream_preview": ("D", "documented progress endpoint; not implemented"),
            "batch": ("I", "offline: batch_size"),
            "remote_cancel": ("I", "offline: /sdapi/v1/interrupt"),
            "native_context": ("N", "stateless txt2img/img2img"),
        },
    ),
)


def matrix() -> tuple[CoverageRoute, ...]:
    if len(ROUTES) != 13:
        raise ValueError("coverage matrix must keep 13 named routes")
    names = [route.name for route in ROUTES]
    if len(set(names)) != len(names):
        raise ValueError("coverage route names must be unique")
    if any("其他" in name or "other public" in name.casefold() for name in names):
        raise ValueError("named coverage routes only; no generic self-hosted placeholder")
    return ROUTES


def transition(
    current: str, target: str, *, live_evidence: bool, offline_evidence: bool, source: bool,
) -> str:
    """A status may only move when the destination evidence exists."""
    if current not in _STATUSES or target not in _STATUSES:
        raise ValueError("unknown coverage status")
    if target == current:
        return target
    if not source:
        raise ValueError("status change requires a located source")
    if current == "T":
        raise ValueError("tested status cannot be demoted without a new review")
    if target == "T" and not live_evidence:
        raise ValueError("tested status requires live evidence")
    if target == "I" and not offline_evidence:
        raise ValueError("implemented status requires offline evidence")
    return target


DIMENSION_KEYS = _KEYS
