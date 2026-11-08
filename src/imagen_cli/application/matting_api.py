"""Replicate-hosted matting: the model catalog, the approval digest, and the run.

Two rules shape this module:

* Nothing leaves the machine until ``--accept-api`` carries the digest of the
  exact request the user reviewed.
* A returned image that does not decode, or whose size disagrees with the
  source, is a failure — never a published artifact.
"""

from __future__ import annotations

import hashlib
import io
import json
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image

import imagen_cli.providers.replicate as replicate
from imagen_cli.domain.primitive import new_id

__all__ = [
    "DEFAULT_MODEL",
    "MODELS",
    "REPLICATE_API_HOST",
    "describe",
    "digest",
    "run",
    "translate_parameters",
]

REPLICATE_API_HOST = "api.replicate.com"

DEFAULT_MODEL = "sprited/birefnet-toonout"

#: Editable per model: what the vendor exposes, what it costs, who licensed it.
MODELS: dict[str, dict[str, Any]] = {
    "sprited/birefnet-toonout": {
        "task": "matting",
        "category": "anime and line art",
        "license": "MIT",
        "source": "https://replicate.com/sprited/birefnet-toonout",
        "note": "ToonOut — a BiRefNet fine-tune for anime and cartoon cutouts",
        "implemented": True,
        "version": "35f7c78ef6338bf4cb94b41a83407ab44e2de7b436b8feafba547235d366a2bb",
        "parameter_style": "mask",
        "parameters": {
            "resolution": {"type": "integer", "minimum": 512, "maximum": 2048,
                           "default": 1024},
            "mask_blur": {"type": "integer", "minimum": 0, "maximum": 128,
                          "default": 0},
            "mask_offset": {"type": "integer", "minimum": -64, "maximum": 64,
                            "default": 0},
            "refine_fg": {"type": "boolean", "default": False},
        },
        "billing": "per-run",
        "cost_note": "vendor-priced per run; a 1 MP cutout typically costs "
                     "a few tenths of a cent",
    },
    "sprited/birefnet": {
        "task": "matting",
        "category": "general, ten BiRefNet variants",
        "license": "MIT",
        "source": "https://replicate.com/sprited/birefnet",
        "note": "Official variant set; this command always uses variant=general, "
                "precision=fp32, output_format=cutout",
        "implemented": True,
        "version": None,
        "parameter_style": "mask",
        "parameters": {
            "resolution": {"type": "integer", "minimum": 512, "maximum": 2048,
                           "default": 1024},
            "mask_blur": {"type": "integer", "minimum": 0, "maximum": 128,
                          "default": 0},
            "mask_offset": {"type": "integer", "minimum": -64, "maximum": 64,
                            "default": 0},
            "refine_fg": {"type": "boolean", "default": False},
        },
        "billing": "per-run",
        "cost_note": "vendor-priced per run; higher resolutions cost more",
    },
    "men1scus/birefnet": {
        "task": "matting",
        "category": "general",
        "license": "MIT",
        "source": "https://replicate.com/men1scus/birefnet",
        "note": "The most-used BiRefNet packaging; takes 'WIDTHxHEIGHT' resolution",
        "implemented": True,
        "version": None,
        "parameter_style": "resolution_label",
        "parameters": {
            "resolution": {"type": "integer", "minimum": 512, "maximum": 2048,
                           "default": 1024},
        },
        "billing": "per-run",
        "cost_note": "vendor-priced per run",
    },
    "m-kunz/birefnet_portrait": {
        "task": "matting",
        "category": "portraits",
        "license": "MIT",
        "source": "https://replicate.com/m-kunz/birefnet_portrait",
        "note": "Portrait-tuned BiRefNet; only the image is sent, the model's own "
                "defaults decide masks and sizing",
        "implemented": True,
        "version": None,
        "parameter_style": "none",
        "parameters": {},
        "billing": "per-run",
        "cost_note": "vendor-priced per run",
    },
    "sprited/birefnet-video": {
        "task": "matting",
        "category": "video",
        "license": "MIT",
        "source": "https://replicate.com/sprited/birefnet-video",
        "note": "Video cutouts are out of scope; listed so the gap is visible",
        "implemented": False,
        "version": None,
        "parameter_style": "mask",
        "parameters": {},
        "billing": "per-second",
        "cost_note": "vendor-priced per second of video",
    },
}


def describe(model: str) -> dict[str, Any]:
    return MODELS[model]


def _failure(kind: str, detail: str) -> dict[str, Any]:
    return {"error": {"type": kind, "detail": detail}}


def digest(source_sha256: str, model: str, parameters: Mapping[str, Any]) -> str:
    """Bind the exact request: source bytes, model and every non-default parameter."""
    payload = {
        "source_sha256": source_sha256,
        "model": model,
        "parameters": {key: value for key, value in parameters.items()
                       if value is not None},
        "target": REPLICATE_API_HOST,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def translate_parameters(model: str, parameters: Mapping[str, Any]) -> dict[str, Any]:
    """Map our one parameter vocabulary onto what this vendor model accepts."""
    style = describe(model)["parameter_style"]
    given = {key: value for key, value in parameters.items() if value is not None}
    if style == "none":
        return {}
    if style == "resolution_label":
        resolution = given.get("resolution")
        return {} if resolution is None else {"resolution": f"{resolution}x{resolution}"}
    return {key: given[key] for key in
            ("resolution", "mask_blur", "mask_offset", "refine_fg")
            if key in given}


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _inside_store(path: Any, store: Any) -> bool:
    """Refuse a path that escapes the store root, whatever produced it."""
    root = getattr(store, "root", None)
    if root is None:
        return True
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
    except (OSError, ValueError):
        return False
    return True


def run(
    repositories: Any,
    store: Any,
    artifact: str,
    *,
    model: str = DEFAULT_MODEL,
    session: str | None = None,
    resolution: int | None = None,
    mask_blur: int | None = None,
    mask_offset: int | None = None,
    refine_fg: bool | None = None,
    accept_api: str | None = None,
    key_ref: str = "env:REPLICATE_API_TOKEN",
    timeout: float = 300.0,
    transport: Any = None,
    predictor: Any = None,
    fetch: Any = None,
) -> tuple[str, dict[str, Any]]:
    """Matte one artifact on Replicate, after an explicit, digest-bound approval."""
    from imagen_cli import i18n
    from imagen_cli.application import watermark_service
    from imagen_cli.execution.configured import resolve_credential

    entry = MODELS.get(model)
    if entry is None:
        return "validation_error", _failure("validation_error", i18n.t(
            f"Unknown matting model {model!r}; API models: "
            f"{', '.join(sorted(MODELS))}",
            f"未知抠图模型 {model!r}；API 模型可选：{', '.join(sorted(MODELS))}",
        ))
    if not entry["implemented"]:
        return "unsupported_capability", _failure("unsupported_capability", i18n.t(
            "Video matting is not implemented; only image cutouts run through the API.",
            "视频抠图尚未实现；API 路径只处理静态图。",
        ))

    row = repositories.get("artifacts", artifact)
    path = store.get(artifact) if artifact else None
    if row is None or path is None or row["save_state"] != "published":
        return "validation_error", _failure("validation_error", i18n.t(
            "The source must be a published artifact.",
            "源必须是已发布的产物。",
        ))
    data = path.read_bytes() if _inside_store(path, store) else None
    if data is None:
        return "validation_error", _failure("validation_error", i18n.t(
            "The source path is outside the artifact store.",
            "源路径不在产物仓库内。",
        ))
    source_sha256 = hashlib.sha256(data).hexdigest()
    if source_sha256 != row["sha256"]:
        return "validation_error", _failure("validation_error", i18n.t(
            "The source artifact bytes changed.",
            "源产物字节已变化。",
        ))

    requested: dict[str, Any] = {
        "resolution": resolution,
        "mask_blur": mask_blur,
        "mask_offset": mask_offset,
        "refine_fg": refine_fg,
    }
    try:
        session_id = watermark_service._session(repositories, artifact, session)
    except ValueError as error:
        return "validation_error", _failure("validation_error", str(error))

    wanted = digest(source_sha256, model, requested)
    if accept_api != wanted:
        return "approval_required", {
            "api": {
                "model": model,
                "version": entry["version"],
                "uploads_to": REPLICATE_API_HOST,
                "input_bytes": len(data),
                "parameters": translate_parameters(model, requested),
                "license": entry["license"],
                "billing": entry["billing"],
                "cost_note": entry["cost_note"],
            },
            "api_digest": wanted,
            "error": {
                "type": "approval_required",
                "detail": i18n.t(
                    f"Matting with {model} uploads the image to "
                    f"{REPLICATE_API_HOST} and is billed by Replicate "
                    f"({entry['cost_note']}). Review the request, then rerun with "
                    f"--accept-api {wanted}",
                    f"用 {model} 抠图会把图片上传到 {REPLICATE_API_HOST}，并按 "
                    f"Replicate 计费（{entry['cost_note']}）。确认请求后，重跑并加上 "
                    f"--accept-api {wanted}",
                ),
            },
        }

    try:
        token = resolve_credential(key_ref)
    except (OSError, ValueError) as error:
        return "unsupported_capability", _failure("unsupported_capability", i18n.t(
            f"Replicate credentials are unavailable ({error}); set "
            f"{key_ref.split(':', 1)[-1]} in this shell.",
            f"Replicate 凭据不可用（{error}）；请在本 shell 设置 "
            f"{key_ref.split(':', 1)[-1]}。",
        ))

    try:
        with Image.open(io.BytesIO(data)) as source_image:
            expected = source_image.size
    except (OSError, ValueError) as error:
        return "validation_error", _failure("validation_error", i18n.t(
            f"The source image cannot be decoded: {error}",
            f"源图片无法解码：{error}",
        ))

    inputs = {"image": replicate.data_uri(data), **translate_parameters(model, requested)}
    started = time.monotonic()
    try:
        prediction = (predictor or replicate.predict)(
            model, inputs, token=token, version=entry["version"],
            timeout=timeout, transport=transport,
        )
        payload = (fetch or (lambda url: replicate.download(
            url, token=token, transport=transport)))(prediction.output[0])
    except replicate.ReplicateError as error:
        return "failed", _failure(error.kind, error.detail)
    latency = round(time.monotonic() - started, 3)

    try:
        with Image.open(io.BytesIO(payload)) as opened:
            opened.load()
            produced = opened.convert("RGBA")
    except (OSError, ValueError) as error:
        return "failed", _failure("failed", i18n.t(
            f"Replicate returned an image that cannot be decoded: {error}",
            f"Replicate 返回的图片无法解码：{error}",
        ))

    if produced.size != expected:
        return "failed", _failure("failed", i18n.t(
            f"Replicate returned {produced.size[0]}x{produced.size[1]} but the "
            f"source is {expected[0]}x{expected[1]}; nothing was published.",
            f"Replicate 返回 {produced.size[0]}x{produced.size[1]}，而源为 "
            f"{expected[0]}x{expected[1]}；未发布任何产物。",
        ))

    identity = new_id("art")
    store.stage(identity, _png_bytes(produced), origin_manifest={
        "kind": "matting",
        "operation": "matting.api",
        "backend": "api:replicate",
        "model": model,
        "version": entry["version"],
        "api_job": prediction.id,
        "source": artifact,
        "source_sha256": source_sha256,
        "parameters": translate_parameters(model, requested),
        "latency_s": latency,
        "verified": False,
    })
    store.publish(identity)
    repositories.insert("artifact_links", {
        "session_id": session_id,
        "artifact_id": identity,
        "role": "output",
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    })
    return "succeeded", {
        "artifact": repositories.get("artifacts", identity),
        "source": artifact,
        "report": {
            "model": model,
            "api_job": prediction.id,
            "latency_s": latency,
            "engine": {"backend": "api:replicate", "model": model,
                       "version": entry["version"]},
            "uploads_to": REPLICATE_API_HOST,
            "verified": False,
        },
    }
