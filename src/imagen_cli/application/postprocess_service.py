"""Local post-processing: a new artifact per pipeline, never an overwrite."""

from __future__ import annotations

import difflib
import hashlib
import inspect
import io
import json
import math
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from threading import Event
from typing import Any

import PIL
from PIL import Image

from imagen_cli import i18n
from imagen_cli.application import media_ops
from imagen_cli.domain.primitive import new_id
from imagen_cli.storage import queries
from imagen_cli.storage.artifacts import ArtifactStore, image_limits
from imagen_cli.storage.repositories import Repositories

MAX_STEPS = 20
MAX_INPUT_PIXELS = 32_000_000
MAX_OUTPUT_PIXELS = 64_000_000
MAX_WORKING_BYTES = 2 * 1024**3
NONDETERMINISTIC_OPS = {"superres"}


def _engine() -> dict[str, str]:
    try:
        package = version("imagen-cli")
    except PackageNotFoundError:  # pragma: no cover - editable source trees
        package = "0.0.0"
    from PIL import features

    engine = {"imagen_cli": package, "pillow": PIL.__version__}
    try:
        engine["numpy"] = version("numpy")
    except PackageNotFoundError:
        pass
    cms = features.version("littlecms2")
    if cms:
        engine["littlecms"] = cms
    return engine


def validate(pipeline: Any) -> list[dict[str, Any]]:
    if not isinstance(pipeline, list) or not pipeline:
        raise ValueError("pipeline must be a non-empty list")
    if len(pipeline) > MAX_STEPS:
        raise ValueError("pipeline exceeds the step limit")
    steps: list[dict[str, Any]] = []
    for index, step in enumerate(pipeline):
        if not isinstance(step, dict) or not isinstance(step.get("op"), str):
            raise ValueError("each step needs an op")
        name = step["op"]
        if name == "encode":
            if index != len(pipeline) - 1:
                raise ValueError("encode must be the final step")
        elif name not in media_ops.OPS:
            matches = difflib.get_close_matches(name, sorted(media_ops.OPS), n=3)
            suggestion = ""
            if matches:
                suggestion = i18n.t(
                    f" Did you mean {', '.join(matches)}?", f"是不是 {'、'.join(matches)}？"
                )
            raise ValueError(
                i18n.t(
                    f"Unknown operation '{name}'.{suggestion} Run `imagen ops` to list them.",
                    f"未知算子 '{name}'。{suggestion}运行 `imagen ops` 查看列表。",
                )
            )
        params = step.get("params", {})
        if not isinstance(params, dict):
            raise ValueError("operation params must be an object")
        extra = set(step) - {"op", "params", "algorithm_version"}
        if extra:
            raise ValueError("unexpected step field")
        revision = step.get("algorithm_version", 1)
        if type(revision) is not int or revision != 1:
            raise ValueError("unsupported algorithm version")
        operation = media_ops.encode if name == "encode" else media_ops.OPS[name]
        signature = inspect.signature(operation)
        try:
            signature.bind(None, **params)
        except TypeError as error:
            accepted = ", ".join(list(signature.parameters)[1:])
            raise ValueError(
                i18n.t(
                    f"Invalid parameters for {name}: {error}. Accepted: {accepted}",
                    f"{name} 的参数不合法：{error}。可用参数：{accepted}",
                )
            ) from error
        def check_finite(value: Any) -> None:
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("operation parameters must be finite")
            if isinstance(value, dict):
                for item in value.values():
                    check_finite(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    check_finite(item)
        check_finite(params)
        normalized = {"op": name, "params": dict(params)}
        if "algorithm_version" in step:
            normalized["algorithm_version"] = revision
        steps.append(normalized)
    return steps


def resolve_steps(pipeline: Any) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    for step in validate(pipeline):
        name = step["op"]
        operation = media_ops.encode if name == "encode" else media_ops.OPS[name]
        bound = inspect.signature(operation).bind(None, **step["params"])
        bound.apply_defaults()
        effective = dict(bound.arguments)
        effective.pop(next(iter(inspect.signature(operation).parameters)))
        for parameter in inspect.signature(operation).parameters.values():
            if parameter.kind == inspect.Parameter.VAR_KEYWORD:
                effective.update(effective.pop(parameter.name, {}))
        details: dict[str, Any] = {}
        module = operation.__module__.rsplit(".", 1)[-1]
        if module in {
            "filters_structure", "filters_advanced", "filters_restoration", "filters_rank",
            "filters_fast", "filters_frequency",
        }:
            details = {
                "working_space": "linear_srgb" if name in {
                    "linear_resize", "chroma_transfer", "illumination_correct"
                } else "encoded_srgb",
                "border": effective.get("border", "reflect"),
                "alpha_policy": "preserve_alpha_coverage_weighted",
                "approximation": None,
                "status": "experimental",
            }
            if module == "filters_structure":
                details.update({
                    "border": {"l0_smooth": "periodic", "domain_transform": "recursive_scan",
                               "wls_smooth": "natural", "rtv_smooth": "natural",
                               "wls_detail": "natural"}[name],
                    "alpha_policy": "preserve_alpha_mean_extend_hidden_rgb",
                })
            if name == "linear_resize":
                details["alpha_policy"] = "linear_coverage_premultiplied_resample"
                details["border"] = "pillow_resampling"
            elif name in {"periodic_notch", "periodic_notch_auto"}:
                details.update({"border": "periodic", "working_space": "encoded_srgb"})
            if name == "local_laplacian_sampled":
                details["alpha_policy"] = "preserve_alpha_mean_extend_hidden_rgb"
            approximations = {
                "local_laplacian_sampled": "sampled_remap_gaussian_bilinear_pyramid",
                "weighted_guided_smooth": "luma_coverage_sigma1_variance_weights",
                "periodic_notch_auto": "hann_plane_detrended_local_peak_detection",
                "fast_guided_smooth": "bilinear_subsampled_luma_coefficients",
                "wls_detail": "fixed_guide_wls_scale_bands",
                "rtv_smooth": "box_window_irls",
                "kuwahara_anisotropic": "cosine_sector_masks",
                "flow_line": "bilinear_euler_streamline",
                "multiscale_detail": "gaussian_telescoping_bands",
                "illumination_correct": "gaussian_max_rgb_illumination",
                "chroma_transfer": "linear_rgb_opponent_statistics",
            }
            details["approximation"] = approximations.get(name)
        resolved.append({
            "op": name, "algorithm_version": 1,
            "implementation": f"{operation.__module__}.{operation.__name__}",
            "effective_params": effective,
            **details,
        })
    return resolved


def replay_status(manifest: dict[str, Any]) -> dict[str, Any] | None:
    """Inspect compatibility only: never execute steps or access recorded paths."""
    if manifest.get("kind") != "postprocess":
        return None
    schema = manifest.get("schema_version")
    if schema is None:
        return {"status": "legacy_ambiguous", "reason": "historical implementation is unbound"}
    if type(schema) is not int or schema != 2:
        return {"status": "unsupported", "reason": "unknown manifest schema"}
    recorded = manifest.get("resolved_pipeline")
    if not isinstance(recorded, list) or not recorded or len(recorded) > MAX_STEPS:
        return {"status": "unavailable", "reason": "missing resolved pipeline"}
    pipeline = []
    for index, step in enumerate(recorded):
        if not isinstance(step, dict) or not {"op", "algorithm_version", "implementation",
                                             "effective_params"} <= step.keys():
            return {"status": "unavailable", "step": index, "reason": "incomplete step"}
        pipeline.append({"op": step["op"], "algorithm_version": step["algorithm_version"],
                         "params": step["effective_params"]})
        try:
            current = resolve_steps(pipeline)[-1]
        except (ValueError, TypeError, KeyError):
            return {"status": "unavailable", "step": index,
                    "reason": "unknown operation, version or parameters"}
        if current != step:
            return {"status": "unavailable", "step": index,
                    "reason": "implementation, defaults or numerical contract changed"}
    if any(step["op"] == "superres" for step in recorded):
        return {"status": "unavailable", "reason": "model execution identity requires verification"}
    if any(step["op"] == "chroma_transfer" for step in recorded):
        return {"status": "external_inputs_required", "reason": "reference bytes must be verified"}
    return {"status": "replayable", "scope": "pipeline_compatible_not_byte_identity"}


def replay_pipeline(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    compatibility = replay_status(manifest)
    if compatibility is None or compatibility["status"] != "replayable":
        raise ValueError(f"manifest cannot replay: {compatibility}")
    return validate([{"op": step["op"], "algorithm_version": step["algorithm_version"],
                      "params": dict(step["effective_params"])}
                     for step in manifest["resolved_pipeline"]])


class BudgetApprovalRequired(ValueError):
    """A valid pipeline needs a decision about its estimated resource use."""

    def __init__(self, report: dict[str, Any]) -> None:
        self.report = report
        width, height = report["output_size"]
        estimated_mib = report["estimated_working_bytes"] / 1024**2
        super().__init__(i18n.t(
            f"{'; '.join(report['reasons'])}: output {width}x{height} "
            f"({report['output_pixels']:,} pixels), estimated working buffers "
            f"{estimated_mib:,.0f} MiB (model/runtime memory additional). "
            "Large processing may exhaust memory; continue only after explicit confirmation.",
            f"{'；'.join(report['reasons'])}：预计输出 {width}×{height} "
            f"（{report['output_pixels']:,} 像素），估算工作缓冲 {estimated_mib:,.0f} MiB"
            "（不含模型与运行时）。大图处理可能耗尽内存，请确认是否继续。",
        ))


def check_budget(
    size: tuple[int, int], pipeline: list[dict[str, Any]], *, allow_large: bool = False,
) -> None:
    # shortcut: estimates are not a hard allocation cap; isolated workers enforce that.
    width, height = size
    reasons: list[str] = []
    peak_pixels = width * height
    working_bytes = 0
    if width <= 0 or height <= 0:
        raise ValueError("input dimensions must be positive")
    if width * height > MAX_INPUT_PIXELS:
        reasons.append("input pixel budget exceeded")
    for step in pipeline:
        name, params = step["op"], step.get("params", {})
        # Conservative buffer estimates, not measured peak RSS or an OS allocation cap.
        bytes_per_pixel = {
            "local_laplacian_sampled": 512, "weighted_guided_smooth": 512,
            "fast_guided_smooth": 512,
            "guided_smooth": 512, "rolling_guidance": 512, "flow_line": 512,
            "l0_smooth": 512, "domain_transform": 256,
            "wls_detail": 512,
            "wls_smooth": 512, "rtv_smooth": 512,
            "bounded_sharpen": 256, "multiscale_detail": 384,
            "periodic_notch_auto": 512,
            "periodic_notch": 384, "chroma_transfer": 384,
            "illumination_correct": 256, "linear_resize": 128,
        }.get(name, 0)
        if name == "weighted_median":
            radius = params.get("radius", 2)
            if type(radius) is not int or not 1 <= radius <= 8:
                raise ValueError("invalid weighted median radius")
            estimate = width * height * 96 + width * (2 * radius + 1)**2 * 128
            working_bytes = max(working_bytes, estimate)
            if estimate > MAX_WORKING_BYTES:
                reasons.append("estimated working memory budget exceeded")
        if name == "kuwahara_anisotropic":
            sectors = params.get("sectors", 8)
            if type(sectors) is not int or sectors not in (4, 8, 12, 16):
                raise ValueError("invalid Kuwahara sectors")
            bytes_per_pixel = 512 + sectors * 56
        working_bytes = max(working_bytes, width * height * bytes_per_pixel)
        if width * height * bytes_per_pixel > MAX_WORKING_BYTES:
            reasons.append("estimated working memory budget exceeded")
        if name in {"resize", "linear_resize", "pad", "perspective"}:
            scale = params.get("scale")
            target_width, target_height = params.get("width"), params.get("height")
            for dimension in (target_width, target_height):
                if dimension is not None and (type(dimension) is not int or dimension <= 0):
                    raise ValueError("target dimensions must be positive integers")
            if scale is not None:
                if type(scale) not in (int, float) or not math.isfinite(scale) or scale <= 0:
                    raise ValueError("scale must be finite and positive")
                if name == "resize" and type(scale) is not int:
                    raise ValueError("resize scale must be an integer")
                if name == "linear_resize" and (target_width is not None
                                              or target_height is not None):
                    raise ValueError("scale and dimensions are mutually exclusive")
                width, height = round(width * scale), round(height * scale)
            elif name in {"pad", "perspective"}:
                width, height = target_width or width, target_height or height
            elif target_width is not None or target_height is not None:
                width, height = (
                    target_width if target_width is not None
                    else round(width * target_height / height),
                    target_height if target_height is not None
                    else round(height * target_width / width),
                )
        elif name in {"block_scale", "scale2x"}:
            factor = params.get("factor", 2)
            if type(factor) is not int or factor <= 0:
                raise ValueError("scale factor must be a positive integer")
            width, height = width * factor, height * factor
        elif name == "transpose" and params.get("mode", "rotate_90") != "rotate_180":
            width, height = height, width
        elif name == "crop":
            box = (params.get("left", 0), params.get("top", 0),
                   params.get("right", width), params.get("bottom", height))
            box = (box[0], box[1], width if box[2] is None else box[2],
                   height if box[3] is None else box[3])
            if any(type(value) is not int for value in box):
                raise ValueError("crop dimensions must be integers")
            width, height = box[2] - box[0], box[3] - box[1]
        elif name == "rotate" and params.get("degrees", 90) % 180:
            width, height = height, width
        elif name == "superres":
            from imagen_cli.application.superres import MANIFEST

            model = params.get("model", "realesrgan-x4plus")
            if model not in MANIFEST:
                raise ValueError("unknown super-resolution model")
            factor = MANIFEST[model]["scale"]
            width, height = width * factor, height * factor
            working_bytes = max(working_bytes, width * height * 16)
        if width <= 0 or height <= 0:
            raise ValueError("target dimensions must be positive")
        peak_pixels = max(peak_pixels, width * height)
        if width * height > MAX_OUTPUT_PIXELS:
            reasons.append("output pixel budget exceeded")
        if name == "linear_resize":
            working_bytes = max(working_bytes, width * height * 128)
            if width * height * 128 > MAX_WORKING_BYTES:
                reasons.append("estimated working memory budget exceeded")
    if reasons and not allow_large:
        raise BudgetApprovalRequired({
            "input_size": list(size), "output_size": [width, height],
            "output_pixels": width * height, "peak_pixels": peak_pixels,
            "estimated_working_bytes": working_bytes,
            "reasons": list(dict.fromkeys(reasons)),
        })


def budget_preflight(path: Path, pipeline: Any, approval: str | None = None) -> dict[str, Any]:
    """Bind a one-run decision to the actual source, normalized steps and estimates."""
    steps = validate(pipeline)
    with image_limits(True), Image.open(path) as opened:
        size = opened.size
    try:
        check_budget(size, steps)
    except BudgetApprovalRequired as error:
        report = error.report
        payload = {"source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                   "pipeline": steps, "budget": report}
        digest = hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode()).hexdigest()
        report["digest"] = digest
        if approval != digest:
            raise BudgetApprovalRequired(report) from None
        return {"budget_approval": digest, "budget": report}
    return {}


def normalize_color(image: Image.Image) -> tuple[Image.Image, dict[str, Any]]:
    from PIL import ImageCms

    if image.mode not in {"1", "L", "LA", "P", "RGB", "RGBA", "CMYK", "LAB"}:
        raise ValueError("unsupported color mode; only 8-bit managed inputs are supported")
    if image.mode in {"CMYK", "LAB"} and not image.info.get("icc_profile"):
        raise ValueError("this color mode requires an embedded ICC profile")
    alpha = image.convert("RGBA").getchannel("A") if (
        "A" in image.getbands() or "transparency" in image.info
    ) else None
    profile = image.info.get("icc_profile")
    record: dict[str, Any] = {"working_space": "srgb", "rendering_intent": "relative_colorimetric"}
    if profile:
        try:
            source_profile = ImageCms.ImageCmsProfile(io.BytesIO(profile))
            color = image if image.mode in ("RGB", "CMYK", "LAB") else image.convert("RGB")
            result = ImageCms.profileToProfile(
                color, source_profile, ImageCms.createProfile("sRGB"),
                renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC, outputMode="RGB",
            )
            if result is None:
                raise ValueError("ICC conversion returned no image")
        except (OSError, ValueError, TypeError, ImageCms.PyCMSError) as error:
            raise ValueError("invalid or unsupported ICC profile") from error
        record.update({"input_profile": "embedded", "backend": "pillow_imagecms",
                       "input_profile_sha256": hashlib.sha256(profile).hexdigest()})
    else:
        result = image.convert("RGB")
        record["input_profile"] = "assumed_srgb"
    if alpha is not None:
        result = result.convert("RGBA")
        result.putalpha(alpha)
    profile_bytes = bytearray(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    # LittleCMS stamps creation time; canonicalize metadata for repeatable encoded bytes.
    import struct

    profile_bytes[24:36] = struct.pack(">6H", 2000, 1, 1, 0, 0, 0)
    profile_bytes[84:100] = bytes(16)
    result.info["icc_profile"] = bytes(profile_bytes)
    record["output_profile"] = "srgb"
    return result, record


def _owner(repositories: Repositories, source: str) -> str:
    live = queries.live_sessions(repositories)
    holders = queries.reference_sessions(repositories).get(source, set()) & live
    if len(holders) == 1:
        return next(iter(holders))
    if not holders:
        raise ValueError("source artifact has no session; pass --session")
    raise ValueError("source artifact belongs to several sessions; pass --session")


def render(path: Any, pipeline: Any, *, allow_large: bool = False) -> tuple[bytes, dict[str, Any]]:
    """Compute bytes and provenance without touching storage or session state."""
    steps = validate(pipeline)
    with image_limits(allow_large), Image.open(path) as opened:
        check_budget(opened.size, steps, allow_large=allow_large)
        opened.load()
        current, color_record = normalize_color(opened)
    output_profile = current.info.get("icc_profile")
    options: dict[str, Any] = {}
    if steps[-1]["op"] == "encode":
        options = steps.pop()["params"]
    references: list[dict[str, Any]] = []
    detections: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        operation = media_ops.OPS[step["op"]]
        params = dict(step["params"])
        if step["op"] == "chroma_transfer":
            from pathlib import Path

            reference_bytes = Path(params["reference"]).read_bytes()
            with Image.open(io.BytesIO(reference_bytes)) as reference:
                check_budget(reference.size, [])
                reference.load()
                normalized_reference, reference_color = normalize_color(reference)
            references.append({"step": index,
                               "sha256": hashlib.sha256(reference_bytes).hexdigest(),
                               "color": reference_color})
            params["reference"] = normalized_reference
        try:
            current = operation(current, **params)
            detection = current.info.pop("postprocess_detection", None)
            if detection is not None:
                detections.append({"step": index, **detection})
        except TypeError as error:
            raise ValueError(f"invalid parameters for {step['op']}") from error
    if output_profile:
        current.info["icc_profile"] = output_profile
    try:
        data = media_ops.encode(current, **options)
    except TypeError as error:
        raise ValueError("invalid encode parameters") from error
    return data, {"color_management": color_record, "references": references,
                  "detections": detections}


def apply(
    repositories: Repositories,
    store: ArtifactStore,
    source: str,
    pipeline: Any,
    *,
    session_id: str | None = None,
    timeout: float | None = None,
    cancelled: Event | None = None,
    budget_approval: str | None = None,
) -> dict[str, Any]:
    steps = validate(pipeline)
    row = repositories.get("artifacts", source, include_private=True)
    if row is None:
        raise ValueError(
            i18n.t(
                f"Artifact {source} was not found in this workspace.",
                f"产物 {source} 不在当前工作区中。",
            )
        )
    if row["save_state"] != "published":
        raise ValueError("source artifact is not published")
    path = store.get(source)
    if path is None:
        raise ValueError("source artifact bytes are unavailable")
    session = session_id or _owner(repositories, source)
    if repositories.get("sessions", session) is None:
        raise ValueError(i18n.t(f"Session {session} was not found.", f"会话 {session} 不存在。"))
    from imagen_cli.application.postprocess_worker import execute

    data, metadata = execute(path, steps, timeout=timeout, cancelled=cancelled,
                             budget_approval=budget_approval)
    if cancelled is not None and cancelled.is_set():
        raise ValueError("postprocess cancelled")
    return publish_result(repositories, store, source, steps, data, metadata, session_id=session)


def publish_result(repositories: Repositories, store: ArtifactStore, source: str,
                   pipeline: Any, data: bytes, metadata: dict[str, Any], *,
                   session_id: str | None = None) -> dict[str, Any]:
    """Publish completed local computation on the storage-owning thread."""
    steps = validate(pipeline)
    row = repositories.get("artifacts", source)
    if row is None or row["save_state"] != "published":
        raise ValueError("source artifact is no longer published")
    session = session_id or _owner(repositories, source)
    if repositories.get("sessions", session) is None:
        raise KeyError(session)
    recorded = [dict(step) for step in steps]
    identity = new_id("art")
    manifest = {
        "kind": "postprocess",
        "schema_version": 2,
        **metadata,
        "source_artifact": source,
        "source_sha256": row["sha256"],
        "pipeline": recorded,
        "resolved_pipeline": resolve_steps(recorded),
        "engine": _engine(),
        "deterministic": not any(step["op"] in NONDETERMINISTIC_OPS for step in recorded),
        "seed": None,
    }
    if metadata.get("budget_approval"):
        manifest.update({"budget_approval": metadata["budget_approval"],
                         "budget": metadata["budget"]})
    store.stage(identity, data, origin_manifest=manifest,
                allow_large=bool(metadata.get("budget_approval")))
    store.publish(identity)
    repositories.insert(
        "artifact_links",
        {
            "session_id": session, "artifact_id": identity, "role": "output",
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        },
    )
    return repositories.get("artifacts", identity) or {}
