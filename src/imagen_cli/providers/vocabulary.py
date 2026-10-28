"""The parameter vocabulary a provider route actually accepts.

Vendor references, checked 2026-10-28:

- OpenAI images (``/images/generations`` and ``/images/edits``):
  https://developers.openai.com/api/reference/resources/images and the OpenAPI
  document it is generated from. ``size`` keeps ``auto`` plus the three standard
  values, and the ``gpt-image-2`` family additionally takes an arbitrary
  ``WIDTHxHEIGHT`` with both edges divisible by 16, an aspect ratio between 1:3
  and 3:1, no edge above 3840 and a total pixel count between 655,360 and
  8,294,400. ``gpt-image-1`` shut down on 2026-10-23; ``dall-e-2`` and
  ``dall-e-3`` left the API on 2026-05-12.
- Gemini image generation (``ImageConfig`` on ``:generateContent``):
  https://ai.google.dev/api/generate-content documents ``aspectRatio`` with
  fourteen values and ``imageSize`` with ``512``, ``1K``, ``2K`` and ``4K``,
  defaulting to ``1K``. Per-model tiers come from
  https://ai.google.dev/gemini-api/docs/image-generation.

Planning, the CLI schema and the TUI read this one table, so a value the plan
accepts is a value the adapter can forward. Routes without a reviewed table
return ``None`` and are left unvalidated.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from imagen_cli.i18n import t

__all__ = ["GEMINI_MODELS", "Spec", "check", "retired", "serves", "specs"]

_STANDARD_SIZES = ("1024x1024", "1536x1024", "1024x1536", "auto")
_EDGE_GRID = 16
_MAX_EDGE = 3840
_MIN_PIXELS = 655_360
_MAX_PIXELS = 8_294_400
_MIN_RATIO = (1, 3)
_MAX_RATIO = (3, 1)

_OPENAI_RETIRED = frozenset({"gpt-image-1", "dall-e-2", "dall-e-3"})
_OPENAI_ARBITRARY_PREFIX = "gpt-image-2"
_OPENAI_EXTRA_QUALITY_PREFIX = "gpt-image-2.5"

_GEMINI_RATIOS = (
    "1:1", "1:4", "4:1", "1:8", "8:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4",
    "9:16", "16:9", "21:9",
)
# gemini-3-pro-image-preview and gemini-3.1-flash-image-preview were shut down on
# 2026-06-25; imagen-* left the Gemini API entirely.
_GEMINI_RETIRED = frozenset(
    {"gemini-3-pro-image-preview", "gemini-3.1-flash-image-preview"}
)
GEMINI_MODELS = (
    "nano-banana-2",
    "gemini-nano-banana-2.1",
    "gemini-3.1-flash-image",
    "gemini-3.1-flash-lite-image",
    "gemini-3-pro-image",
    "gemini-2.5-flash-image",
)
_GEMINI_SIZES = {
    "gemini-nano-banana-2.1": ("1K", "2K", "4K"),
    "gemini-3.1-flash-image": ("512", "1K", "2K", "4K"),
    "gemini-3-pro-image": ("1K", "2K", "4K"),
    "gemini-3.1-flash-lite-image": ("1K",),
    "gemini-2.5-flash-image": (),
}
_GEMINI_DEFAULT_SIZES = ("1K", "2K", "4K")


@dataclass(frozen=True)
class Spec:
    """One parameter a route accepts, as the surface names it."""

    name: str
    kind: str
    values: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None
    mode: str = "both"  # "both" | "generate" | "edit"


def _count_spec(operation: str, minimum: int, maximum: int) -> Spec:
    return Spec("count", "count", minimum=minimum, maximum=maximum, mode=operation)


def _leaf(model: str) -> str:
    """The vendor id behind a gateway namespace such as ``A6N/gpt-image-1.5``."""
    return model.rsplit("/", 1)[-1]


def serves(provider: str, family: str, model: str) -> bool:
    """Whether this route recognises the model id at all."""
    leaf = _leaf(model)
    if (provider, family) == ("openai", "images"):
        return leaf.startswith(("gpt-image-", "chatgpt-image-")) and leaf not in _OPENAI_RETIRED
    if (provider, family) == ("gemini", "generate_content"):
        return leaf in GEMINI_MODELS
    return False


def retired(provider: str, family: str, model: str) -> bool:
    """Whether the vendor has shut this model id down."""
    leaf = _leaf(model)
    if (provider, family) == ("openai", "images"):
        return leaf in _OPENAI_RETIRED
    if (provider, family) == ("gemini", "generate_content"):
        return leaf in _GEMINI_RETIRED
    return False


def specs(
    provider: str, family: str, model: str, operation: str = "generate"
) -> tuple[Spec, ...] | None:
    """Parameters the route accepts, or ``None`` when no table covers it."""
    leaf = _leaf(model)
    if (provider, family) == ("openai", "images"):
        table = _openai_specs(leaf, operation)
    elif (provider, family) == ("gemini", "generate_content"):
        table = _gemini_specs(leaf, operation)
    else:
        return None
    return tuple(spec for spec in table if spec.mode in {operation, "both"})


def check(
    provider: str,
    family: str,
    model: str,
    operation: str,
    parameters: Mapping[str, Any],
) -> str | None:
    """Localized reason the route would refuse these parameters, or ``None``."""
    table = specs(provider, family, model, operation)
    if table is None:
        return None
    if not table:
        return t(
            f"{provider} {family} does not serve {model}; choose a current model id.",
            f"{provider} {family} 不提供 {model}；请改用在役模型 id。",
        )
    known = {spec.name: spec for spec in table}
    unknown = sorted(set(parameters) - set(known))
    if unknown:
        return t(
            f"{', '.join(unknown)} is not a parameter this route accepts; it takes "
            f"{', '.join(sorted(known))}.",
            f"{'、'.join(unknown)} 不是该路线接受的参数；可用参数是 {'、'.join(sorted(known))}。",
        )
    for name, spec in known.items():
        if name not in parameters:
            continue
        reason = _refuse(spec, parameters[name], model)
        if reason is not None:
            return reason
    return None


def _refuse(spec: Spec, value: object, model: str) -> str | None:
    if spec.kind == "count":
        if type(value) is not int or not (
            (spec.minimum or 1) <= value <= (spec.maximum or 1)
        ):
            return t(
                f"count must be an integer from {spec.minimum} to {spec.maximum} on this route.",
                f"该路线的 count 必须是 {spec.minimum} 到 {spec.maximum} 之间的整数。",
            )
        return None
    if spec.kind == "range":
        if type(value) is not int or not (
            (spec.minimum or 0) <= value <= (spec.maximum or 0)
        ):
            return t(
                f"{spec.name} must be an integer from {spec.minimum} to {spec.maximum}.",
                f"{spec.name} 必须是 {spec.minimum} 到 {spec.maximum} 之间的整数。",
            )
        return None
    if not isinstance(value, str) or not value:
        return t(f"{spec.name} must be a non-empty string.", f"{spec.name} 必须是非空字符串。")
    if value in spec.values:
        return None
    if spec.kind == "dimensions":
        return _refuse_dimensions(spec, value, model)
    return t(
        f"{spec.name} must be one of {', '.join(spec.values)}.",
        f"{spec.name} 只能是 {', '.join(spec.values)} 之一。",
    )


def _refuse_dimensions(spec: Spec, value: str, model: str) -> str | None:
    rule = _size_rule(model)
    width, separator, height = value.partition("x")
    if not separator or not width.isdigit() or not height.isdigit():
        return rule
    wide, tall = int(width), int(height)
    if wide % _EDGE_GRID or tall % _EDGE_GRID:
        return rule
    if wide > _MAX_EDGE or tall > _MAX_EDGE:
        return rule
    if wide * _MIN_RATIO[1] < tall * _MIN_RATIO[0] or wide * _MAX_RATIO[1] > tall * _MAX_RATIO[0]:
        return rule
    if not _MIN_PIXELS <= wide * tall <= _MAX_PIXELS:
        return rule
    return None


def _size_rule(model: str) -> str:
    return t(
        "size must be auto, one of "
        f"{', '.join(_STANDARD_SIZES[:-1])}, or WIDTHxHEIGHT with both edges "
        f"divisible by {_EDGE_GRID}, an aspect ratio between 1:3 and 3:1, no edge "
        f"above {_MAX_EDGE} and 655,360 to 8,294,400 pixels in total.",
        f"size 只能是 auto、{', '.join(_STANDARD_SIZES[:-1])} 之一，或 WIDTHxHEIGHT："
        f"宽高都必须是 {_EDGE_GRID} 的倍数，宽高比在 1:3 与 3:1 之间，单边不超过 "
        f"{_MAX_EDGE}，总像素在 655360 到 8294400 之间。",
    )


def _openai_specs(model: str, operation: str) -> tuple[Spec, ...]:
    if model in _OPENAI_RETIRED:
        return ()
    quality: tuple[str, ...] = ("auto", "low", "medium", "high")
    if model.startswith(_OPENAI_EXTRA_QUALITY_PREFIX):
        quality = (*quality, "xhigh", "max")
    size = (
        Spec("size", "dimensions", _STANDARD_SIZES)
        if model.startswith(_OPENAI_ARBITRARY_PREFIX)
        else Spec("size", "enum", _STANDARD_SIZES)
    )
    return (
        _count_spec(operation, 1, 10),
        size,
        Spec("quality", "enum", quality),
        Spec("background", "enum", ("auto", "opaque", "transparent")),
        Spec("output_format", "enum", ("png", "jpeg", "webp")),
        Spec("output_compression", "range", minimum=0, maximum=100),
        Spec("moderation", "enum", ("auto", "low")),
        Spec("input_fidelity", "enum", ("low", "high"), mode="edit"),
    )


def _gemini_specs(model: str, operation: str) -> tuple[Spec, ...]:
    if model not in GEMINI_MODELS:
        # The adapter refuses these ids too, so the plan refuses them first.
        return ()
    sizes = _GEMINI_SIZES.get(model, _GEMINI_DEFAULT_SIZES)
    table = [
        _count_spec(operation, 1, 1),
        Spec("aspect_ratio", "enum", _GEMINI_RATIOS),
    ]
    if sizes:
        table.append(Spec("image_size", "enum", sizes))
    return tuple(table)
