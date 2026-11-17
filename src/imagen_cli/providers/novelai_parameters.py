"""Typed, offline NovelAI JSON parameters. Uploads are never parameter values.

Bounds on structured prompts are local resource limits, not model capabilities.
No model is inferred from its name, and no prompt/quality defaults are inserted.
"""
from collections.abc import Mapping
from typing import Annotated, Any

from pydantic import Field

from imagen_cli.domain.primitive import FiniteNumber, StrictModel

Text = Annotated[str, Field(max_length=65536)]
Coordinate = Annotated[FiniteNumber, Field(ge=0, le=1)]


class _Center(StrictModel):
    x: Coordinate
    y: Coordinate


class _Character(StrictModel):
    char_caption: Text
    centers: Annotated[list[_Center], Field(max_length=64)]


class _Caption(StrictModel):
    base_caption: Text
    char_captions: Annotated[list[_Character], Field(max_length=64)]


class _Prompt(StrictModel):
    caption: _Caption
    use_coords: bool | None = None
    use_order: bool | None = None


# Shared vocabulary metadata; actual checks live in normalize_parameters.
PARAMETER_KINDS = {
    "size": "dimensions", "width": "integer", "height": "integer",
    "count": "count", "n_samples": "count", "steps": "integer",
    "seed": "integer", "scale": "number", "negative_prompt": "text",
    "cfg_rescale": "number", "sampler": "text", "noise_schedule": "text",
    "strength": "number", "noise": "number", "sm": "boolean",
    "sm_dyn": "boolean", "dynamic_thresholding": "boolean",
    "v4_prompt": "object", "v4_negative_prompt": "object",
}


def normalize_parameters(
    parameters: Mapping[str, Any], *, model: str, operation: str,
) -> dict[str, Any]:
    """Return wire values, refusing ambiguous aliases and unapproved uploads.

    Only count=1 is declared until a reviewed model capability allows more.
    Integer/numeric bounds below are structural, not service-specific step caps.
    """
    if operation not in {"generate", "edit"}:
        raise ValueError("NovelAI supports generate or edit, not masks/inpainting")
    if operation == "edit" and not {"strength", "noise"} <= parameters.keys():
        raise ValueError("NovelAI edit requires explicit strength and noise")
    if operation == "generate" and {"strength", "noise"} & parameters.keys():
        raise ValueError("NovelAI generate does not accept strength or noise")
    unknown = set(parameters) - PARAMETER_KINDS.keys()
    if unknown:
        raise ValueError(f"NovelAI does not accept parameters: {', '.join(sorted(unknown))}")
    result = dict(parameters)
    if "size" in result:
        if "width" in result or "height" in result:
            raise ValueError("size conflicts with width/height")
        size = result.pop("size")
        if not isinstance(size, str):
            raise ValueError("size must be WIDTHxHEIGHT")
        width, separator, height = size.partition("x")
        if not separator or not width.isascii() or not height.isascii() or not (
            width.isdigit() and height.isdigit()
        ) or len(width) > 10 or len(height) > 10:
            raise ValueError("size must be positive integer WIDTHxHEIGHT")
        result.update(width=int(width), height=int(height))
    if ("width" in result) != ("height" in result):
        raise ValueError("width and height must be supplied together")
    if "count" in result and "n_samples" in result:
        raise ValueError("count conflicts with n_samples")
    result["n_samples"] = result.pop("count", result.get("n_samples", 1))
    for name, value in tuple(result.items()):
        kind = PARAMETER_KINDS[name]
        if kind in {"integer", "count"}:
            minimum = 0 if name == "seed" else 1
            if type(value) is not int or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")
            if kind == "count" and value != 1:
                raise ValueError("count above one requires a declared model capability")
        elif kind == "number":
            # Pydantic's strict float permits integers but never booleans.
            from pydantic import TypeAdapter
            number = TypeAdapter(FiniteNumber).validate_python(value)
            if number < 0 or (name in {"strength", "noise", "cfg_rescale"} and number > 1):
                raise ValueError(f"{name} is outside its supported numeric range")
            result[name] = number
        elif kind == "boolean":
            if type(value) is not bool:
                raise ValueError(f"{name} must be a boolean")
        elif kind == "text":
            if not isinstance(value, str) or len(value) > 65536 or (
                name != "negative_prompt" and not value
            ):
                raise ValueError(f"{name} must be a bounded string")
        elif kind == "object":
            result[name] = _Prompt.model_validate(value).model_dump(exclude_none=True)
    return result


def validate_request_shape(
    *, operation: str, primary_count: int, mask: object = None,
    native_context: object = None,
) -> None:
    """Offline image-role boundary, shared by planning and adapter defense."""
    if mask is not None or native_context is not None:
        raise ValueError("NovelAI does not support masks or native context")
    if operation == "edit":
        if primary_count != 1:
            raise ValueError("NovelAI edit requires exactly one primary image")
    elif operation != "generate" or primary_count:
        raise ValueError("NovelAI generate requires no primary images; select explicit references")
