"""Offline reference validation and deterministic precise-image preparation.

Encoded Vibe content must come from a verified preparation cache, never a parameter
string. Encoded dispatch requires private cache bytes and exact provenance.
"""

import base64
import hashlib
import io
import math
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from PIL import Image, ImageOps

from imagen_cli.storage.artifacts import inspect_media

from .base import ReferenceInput

PRECISE_ALGORITHM = "fit-pad-black-png-v1"


def prepare_precise_reference(content: bytes) -> bytes:
    inspect_media(content)
    with Image.open(io.BytesIO(content)) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")
        width, height = image.size
        canvas = (
            (1024, 1536) if width < height else (1536, 1024) if width > height else (1472, 1472)
        )
        fitted = ImageOps.contain(image, canvas, Image.Resampling.LANCZOS)
        result = Image.new("RGB", canvas, "black")
        result.paste(
            fitted, ((canvas[0] - fitted.width) // 2, (canvas[1] - fitted.height) // 2), fitted
        )
        output = io.BytesIO()
        result.save(output, format="PNG", optimize=False, compress_level=9)
        return output.getvalue()


def _number(settings: Any, key: str, default: float) -> float:
    value = settings.get(key, default)
    if type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{key} must be finite and between zero and one")
    return float(value)


def map_references(
    references: tuple[ReferenceInput, ...],
    *,
    mode: str,
    model: str,
    require_approved: bool = False,
) -> dict[str, Any]:
    if not references:
        return {}
    if sum(len(item.prepared_content if item.prepared_content is not None else item.content)
           for item in references) > 256 * 1024 * 1024:
        raise ValueError("reference upload byte limit exceeded")
    if len(references) > 100 or len({item.kind for item in references}) != 1:
        raise ValueError("Vibe and precise references cannot be combined")
    images, strengths, extraction, secondary, descriptions = [], [], [], [], []
    for reference in references:
        settings = reference.settings
        if reference.kind == "vibe":
            if set(settings) - {"strength", "information_extracted"}:
                raise ValueError("unsupported Vibe settings")
            inspect_media(reference.content)
            if mode == "encoded":
                content = reference.prepared_content
                evidence = reference.encoding
                if (
                    not content
                    or not evidence
                    or evidence.get("encoding_sha256") != hashlib.sha256(content).hexdigest()
                    or evidence.get("byte_size") != len(content)
                    or evidence.get("identity", {}).get("source_sha256")
                    != hashlib.sha256(reference.content).hexdigest()
                    or evidence.get("identity", {}).get("model") != model
                    or evidence.get("identity", {}).get("information_extracted")
                    != _number(settings, "information_extracted", 1)
                ):
                    raise ValueError("encoded Vibe requires verified approved preparation")
            elif mode == "raw":
                content = reference.content
            else:
                raise ValueError("unsupported reference transport")
        else:
            if model not in {"nai-diffusion-4-5-full", "nai-diffusion-4-5-curated"}:
                raise ValueError("precise-reference model support is unverified")
            if set(settings) - {"strength", "information_extracted", "fidelity", "mode"}:
                raise ValueError("unsupported precise settings")
            descriptor = settings.get("mode", "character")
            if descriptor not in {"character", "character&style"}:
                raise ValueError("unsupported precise descriptor")
            descriptions.append({"caption": {"base_caption": descriptor, "char_captions": []}})
            secondary.append(_number(settings, "fidelity", 1))
            if reference.prepared_content is not None:
                content = reference.prepared_content
                if _transformation(content) != dict(reference.transformation):
                    raise ValueError("precise transformation drifted")
            elif require_approved:
                raise ValueError("precise reference requires approved transformation")
            else:
                content = prepare_precise_reference(reference.content)
        images.append(base64.b64encode(content).decode("ascii"))
        strengths.append(_number(settings, "strength", 1))
        extraction.append(_number(settings, "information_extracted", 1))
    if references[0].kind == "vibe":
        return {
            "reference_image_multiple": images,
            "reference_strength_multiple": strengths,
            "reference_information_extracted_multiple": extraction,
        }
    return {
        "director_reference_images": images,
        "director_reference_descriptions": descriptions,
        "director_reference_strength_values": strengths,
        "director_reference_information_extracted": extraction,
        "director_reference_secondary_strength_values": secondary,
    }


def _transformation(content: bytes) -> dict[str, Any]:
    inspect_media(content)
    with Image.open(io.BytesIO(content)) as image:
        return {
            "algorithm": PRECISE_ALGORITHM,
            "width": image.width,
            "height": image.height,
            "sha256": hashlib.sha256(content).hexdigest(),
        }


def reference_transformation(reference: ReferenceInput) -> dict[str, Any]:
    """Approval-visible version and hash of the exact deterministic upload."""
    if reference.kind != "precise":
        return {}
    return _transformation(prepare_precise_reference(reference.content))


def bind_precise_reference(
    reference: ReferenceInput,
    *,
    expected: Mapping[str, Any] | None = None,
) -> ReferenceInput:
    """Prepare once, verify approved evidence, and freeze the exact upload bytes."""
    if reference.kind != "precise":
        return reference
    content = prepare_precise_reference(reference.content)
    evidence = _transformation(content)
    if expected is not None and evidence != dict(expected):
        raise ValueError("approved precise transformation drifted")
    return replace(reference, prepared_content=content, transformation=evidence)


def reference_encoding(reference: ReferenceInput) -> dict[str, Any]:
    """Public provenance only, never the opaque private encoding bytes."""
    if not reference.encoding:
        return {}
    result = dict(reference.encoding)
    result["identity"] = dict(result["identity"])
    return result
