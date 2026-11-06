"""JSON-in/JSON-out bridge executed by the isolated raiw interpreter.

The main process never imports ``remove_ai_watermarks``. Every failure becomes a
structured result; a traceback never crosses this boundary.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

OPERATIONS = {
    "probe",
    "matte",
    "watermark.identify",
    "watermark.metadata",
    "watermark.visible",
    "watermark.erase",
    "watermark.classify",
    "watermark.all",
    "watermark.invisible",
    "watermark.batch",
    "watermark.video.identify",
    "watermark.video.metadata",
    "watermark.video.visible",
    "watermark.video.all",
    "watermark.video.invisible",
    "watermark.video.batch",
}


def _version() -> str | None:
    try:
        from importlib import metadata

        return metadata.version("remove-ai-watermarks")
    except Exception:  # noqa: BLE001 - any import failure means "unknown"
        return None


def _capabilities() -> dict[str, bool]:
    checks = {
        "identify": ("remove_ai_watermarks.identify", "identify"),
        "metadata": ("remove_ai_watermarks.metadata", "strip_and_verify"),
        "visible": ("remove_ai_watermarks", "remove_visible_detailed"),
        "erase": ("remove_ai_watermarks.region_eraser", "erase"),
        "all": ("remove_ai_watermarks.api", "remove_all"),
        "classify": ("remove_ai_watermarks.classify", "classify_pixels"),
        "source_classify": ("remove_ai_watermarks.source_classify", "classify_source"),
        "invisible": ("remove_ai_watermarks.invisible_engine", "InvisibleEngine"),
        "video": ("remove_ai_watermarks.video", "remove_video_all"),
        "video_invisible": ("remove_ai_watermarks.video", "remove_video_invisible"),
    }
    result: dict[str, bool] = {}
    for name, (module, symbol) in checks.items():
        try:
            imported = __import__(module, fromlist=[symbol])
            result[name] = hasattr(imported, symbol)
        except Exception:  # noqa: BLE001 - a missing extra is a capability gap
            result[name] = False
    return result


def _cuda() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001 - torch is optional
        return False


def _probe() -> dict[str, Any]:
    capabilities = _capabilities()
    capabilities["matte"] = _matting_ready()
    return {
        "status": "succeeded",
        "raiw_version": _version(),
        "capabilities": capabilities,
        "cuda": _cuda(),
    }


def _matting_ready() -> bool:
    """True only when the runtime and every pinned matting weight are present."""
    try:
        from imagen_cli.application import matting

        return bool(matting.available_providers()) and all(
            matting.weights_path(name).is_file() for name in matting.MANIFEST
        )
    except Exception:  # noqa: BLE001 - a missing extra is a capability gap
        return False


def _matte(source: Path, output: Path, params: dict[str, Any]) -> dict[str, Any]:
    from PIL import Image

    from imagen_cli.application import matting

    model = str(params.get("model", "birefnet-lite"))
    if model not in matting.MANIFEST:
        raise ValueError(f"unknown matting model: {model}")
    with Image.open(source) as opened:
        opened.load()
        result, engine = matting.matte(
            opened,
            model=model,
            backend=params.get("backend"),
            tile=int(params.get("tile") or 0),
            overlap=int(params.get("overlap") or 64),
            feather=int(params.get("feather") or 0),
            edge_shift=float(params.get("edge_shift") or 0.0),
            accept=False,
        )
    result.save(output, format="PNG")
    return {"status": "succeeded", "report": {"engine": engine, "model": model}}


def _identify(source: Path) -> dict[str, Any]:
    from remove_ai_watermarks.identify import identify

    report = identify(source)
    payload = report.to_dict(schema_version=1) if hasattr(report, "to_dict") else {}
    return {"status": "succeeded", "report": payload}


def _metadata(source: Path, output: Path) -> dict[str, Any]:
    from remove_ai_watermarks.metadata import has_ai_metadata, strip_and_verify

    had = has_ai_metadata(source)
    written, surviving = strip_and_verify(source, output)
    if surviving:
        raise RuntimeError("AI metadata survives the strip; output was not published")
    return {
        "status": "succeeded",
        "report": {"had_ai_metadata": bool(had), "surviving": {}},
        "output": str(written),
    }


def _write_bgr(output: Path, image: Any, alpha: Any) -> None:
    from remove_ai_watermarks import image_io
    from remove_ai_watermarks.metadata import strip_written_output

    if not image_io.write_bgr_with_alpha(output, image, alpha):
        raise OSError("the result image could not be written")
    if leftover := strip_written_output(output):
        raise RuntimeError("AI metadata survives the strip: " + ", ".join(sorted(leftover)))


def _visible_named(source: Path, output: Path, params: dict[str, Any]) -> dict[str, Any]:
    from remove_ai_watermarks import image_io, watermark_registry
    from remove_ai_watermarks.api import visible_provenance

    image, alpha = image_io.read_bgr_and_alpha(source)
    if image is None:
        raise ValueError("the source image could not be decoded")
    mark = watermark_registry.get_mark(str(params["mark"]))
    sensitivity = params.get("sensitivity", "auto")
    provenance = visible_provenance(source)
    trust = watermark_registry.resolve_trust(
        mark.key, sensitivity=sensitivity, provenance=provenance, strict_keys=set(),
    )
    relax = trust != "strict"
    detection = mark.detect(image, provenance=relax)
    if not detection.detected:
        _write_bgr(output, image, alpha)
        return {"status": "no_watermark", "report": {
            "status": "no_watermark", "label": mark.label, "marks": [],
        }}
    result, _ = mark.remove(
        image, backend=params.get("backend", "auto"), provenance=relax,
        force=False, detection=detection,
    )
    _write_bgr(output, result, alpha)
    return {"status": "cleaned", "report": {
        "status": "cleaned", "label": mark.label,
        "marks": [{
            "label": mark.label, "status": "cleaned",
            "confidence_before": detection.confidence, "confidence_after": None,
        }],
    }}


def _visible(source: Path, output: Path, params: dict[str, Any]) -> dict[str, Any]:
    if params.get("mark") not in (None, "auto"):
        return _visible_named(source, output, params)
    import remove_ai_watermarks as raiw

    report = raiw.remove_visible_detailed(
        str(source), str(output),
        **{key: params[key] for key in ("backend", "sensitivity") if key in params},
    )
    marks = [
        {
            "label": mark.label,
            "status": mark.status,
            "confidence_before": mark.confidence_before,
            "confidence_after": mark.confidence_after,
        }
        for mark in getattr(report, "marks", ())
    ]
    return {"status": report.status, "report": {"status": report.status, "marks": marks}}


def _erase(source: Path, output: Path, params: dict[str, Any]) -> dict[str, Any]:
    from remove_ai_watermarks import image_io
    from remove_ai_watermarks.region_eraser import erase

    image, alpha = image_io.read_bgr_and_alpha(source)
    if image is None:
        raise ValueError("the source image could not be decoded")
    boxes = [
        tuple(int(part) for part in raw.replace(" ", "").split(","))
        for raw in params["regions"]
    ]
    result = erase(
        image, boxes=boxes, backend=params.get("backend", "cv2"),
        dilate=int(params.get("dilate", 3)),
        cv2_method=params.get("inpaint_method", "telea"),
    )
    _write_bgr(output, result, alpha)
    return {"status": "succeeded", "report": {
        "regions": len(boxes), "backend": params.get("backend", "cv2"),
    }}


def _classify(source: Path, params: dict[str, Any]) -> dict[str, Any]:
    if params.get("source"):
        from remove_ai_watermarks import source_classify

        if not source_classify.is_available():
            raise RuntimeError(
                "source classification needs the 'source-classify' extra; "
                "run: imagen watermark setup --extras visible,source-classify --accept"
            )
        return {"status": "succeeded",
                "report": source_classify.classify_source(source).to_dict()}
    from remove_ai_watermarks import classify as classify_mod

    if not classify_mod.is_available():
        raise RuntimeError(
            "photo classification needs the 'classify' extra; "
            "run: imagen watermark setup --extras visible,classify --accept"
        )
    return {"status": "succeeded",
            "report": classify_mod.classify_pixels(source).to_dict()}


def _all(source: Path, output: Path, params: dict[str, Any]) -> dict[str, Any]:
    from remove_ai_watermarks.api import InvisibleOptions, remove_all

    stages: list[dict[str, str]] = []

    def progress(stage: str, detail: str) -> None:
        stages.append({"stage": stage, "detail": detail})

    values = {
        "strength": params.get("strength"),
        "pipeline": params.get("pipeline", "sdxl-zimage"),
        "vendor": params.get("vendor"),
        "seed": params.get("seed"),
        "humanize": params.get("humanize", 0.0),
        "unsharp": params.get("unsharp", 0.0),
        "adaptive_polish": params.get("adaptive_polish"),
        "max_resolution": params.get("max_resolution", 0),
        "controlnet_conditioning_scale": params.get("controlnet_conditioning_scale", 1.0),
        "cpu_offload": bool(params.get("cpu_offload")),
        "tile": bool(params.get("tile")),
        "tile_size": params.get("tile_size", 1024),
        "tile_overlap": params.get("tile_overlap", 128),
        "text_manifest": (Path(params["text_manifest"])
                          if params.get("text_manifest") else None),
        "fidelity_anchor": bool(params.get("fidelity_anchor")),
    }
    options = InvisibleOptions(**{key: value for key, value in values.items()
                                  if value is not None})
    result = remove_all(
        source, output, backend=params.get("backend", "auto"),
        sensitivity=params.get("sensitivity", "auto"), invisible=options,
        force=bool(params.get("force")), progress=progress,
    )
    marks = [
        {
            "label": mark.label,
            "status": mark.status,
            "confidence_before": mark.confidence_before,
            "confidence_after": mark.confidence_after,
        }
        for mark in getattr(result, "visible_marks", ())
    ]
    return {"status": "succeeded", "report": {
        "visible_label": result.visible_label,
        "visible_status": result.visible_status,
        "visible_marks": marks,
        "invisible": result.invisible,
        "stages": stages,
    }}


def _invisible(source: Path, output: Path, params: dict[str, Any]) -> dict[str, Any]:
    from remove_ai_watermarks.invisible_engine import InvisibleEngine

    pipeline = params.get("pipeline", "sdxl-zimage")
    engine = InvisibleEngine(pipeline=pipeline, cpu_offload=bool(params.get("cpu_offload")))
    options = {
        key: params[key] for key in
        ("strength", "seed", "vendor", "humanize", "unsharp", "tile", "tile_size",
         "tile_overlap", "max_resolution", "adaptive_polish", "text_manifest",
         "controlnet_conditioning_scale", "fidelity_anchor")
        if key in params
    }
    engine.remove_watermark(source, output, **options)
    return {"status": "succeeded", "report": {"pipeline": pipeline}}


def _jsonable(value: Any) -> Any:
    from dataclasses import asdict, is_dataclass

    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


def _video_identify(source: Path, params: dict[str, Any]) -> dict[str, Any]:
    from remove_ai_watermarks.video import identify_video

    report = identify_video(source, check_visible=params.get("check_visible", True))
    return {"status": "succeeded", "report": _jsonable(report)}


def _video_metadata(source: Path, output: Path) -> dict[str, Any]:
    from remove_ai_watermarks.video import inspect_video_metadata, remove_video_metadata

    report = inspect_video_metadata(source)
    result = remove_video_metadata(source, output)
    return {"status": "succeeded",
            "report": {"check": _jsonable(report), "result": _jsonable(result)}}


def _video_visible(source: Path, output: Path, params: dict[str, Any]) -> dict[str, Any]:
    from remove_ai_watermarks.video import remove_video_visible

    result = remove_video_visible(
        source, output, mark=params.get("mark", "auto"),
        backend=params.get("backend", "cv2"),
        temporal_consistency=params.get("temporal_consistency", True),
    )
    return {"status": "succeeded" if result.output else "no_watermark",
            "report": _jsonable(result)}


def _video_all(source: Path, output: Path, params: dict[str, Any]) -> dict[str, Any]:
    from remove_ai_watermarks.video import remove_video_all

    options = {
        "mark": params.get("mark", "auto"),
        "backend": params.get("backend", "cv2"),
        "temporal_consistency": params.get("temporal_consistency", True),
        "include_invisible": bool(params.get("include_invisible")),
        "noise_std": params.get("noise_std"),
        "long_side": params.get("long_side"),
        "fps": params.get("fps"),
        "batch_size": params.get("batch_size"),
        "seed": params.get("seed"),
    }
    result = remove_video_all(
        source, output, **{key: value for key, value in options.items()
                           if value is not None},
    )
    return {"status": "succeeded", "report": _jsonable(result)}


def _video_invisible(source: Path, output: Path, params: dict[str, Any]) -> dict[str, Any]:
    from remove_ai_watermarks.video import remove_video_invisible

    options = {
        "noise_std": params.get("noise_std"),
        "long_side": params.get("long_side"),
        "fps": params.get("fps"),
        "batch_size": params.get("batch_size"),
        "seed": params.get("seed"),
        "model": params.get("model"),
        "device": params.get("device"),
    }
    result = remove_video_invisible(
        source, output, **{key: value for key, value in options.items()
                           if value is not None},
    )
    return {"status": "succeeded", "report": _jsonable(result)}


def _invisible_options(params: dict[str, Any]) -> Any:
    from remove_ai_watermarks.api import InvisibleOptions

    values = {
        "strength": params.get("strength"),
        "pipeline": params.get("pipeline", "sdxl-zimage"),
        "vendor": params.get("vendor"),
        "seed": params.get("seed"),
        "humanize": params.get("humanize", 0.0),
        "unsharp": params.get("unsharp", 0.0),
        "adaptive_polish": params.get("adaptive_polish"),
        "max_resolution": params.get("max_resolution", 0),
        "controlnet_conditioning_scale": params.get("controlnet_conditioning_scale", 1.0),
        "cpu_offload": bool(params.get("cpu_offload")),
        "tile": bool(params.get("tile")),
        "tile_size": params.get("tile_size", 1024),
        "tile_overlap": params.get("tile_overlap", 128),
        "text_manifest": (Path(params["text_manifest"])
                          if params.get("text_manifest") else None),
        "fidelity_anchor": bool(params.get("fidelity_anchor")),
    }
    return InvisibleOptions(**{key: value for key, value in values.items()
                               if value is not None})


def _image_batch(request: dict[str, Any]) -> dict[str, Any]:
    from remove_ai_watermarks.api import remove_batch

    params = request.get("params") or {}
    summary = remove_batch(
        request["directory"], request["output_directory"],
        mode=params.get("mode", "all"), backend=params.get("backend", "auto"),
        sensitivity=params.get("sensitivity", "auto"),
        invisible=_invisible_options(params), force=bool(params.get("force")),
    )
    return {"status": "succeeded", "report": _jsonable(summary)}


def _video_batch(request: dict[str, Any]) -> dict[str, Any]:
    from remove_ai_watermarks.video import remove_video_batch

    params = request.get("params") or {}
    options = {
        "mark": params.get("mark", "auto"),
        "backend": params.get("backend", "cv2"),
        "temporal_consistency": params.get("temporal_consistency", True),
        "include_invisible": bool(params.get("include_invisible")),
        "noise_std": params.get("noise_std"),
        "long_side": params.get("long_side"),
        "fps": params.get("fps"),
        "batch_size": params.get("batch_size"),
        "seed": params.get("seed"),
    }
    result = remove_video_batch(
        request["directory"], request["output_directory"],
        mode=params.get("mode", "all"),
        **{key: value for key, value in options.items() if value is not None},
    )
    return {"status": "succeeded", "report": _jsonable(result)}


def handle(request: dict[str, Any]) -> dict[str, Any]:
    if request.get("schema_version") != SCHEMA_VERSION:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "error": {"type": "incompatible_worker", "message": "unsupported schema_version"},
        }
    operation = request.get("operation")
    if operation not in OPERATIONS:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "error": {"type": "validation_error", "message": f"unknown operation: {operation}"},
        }
    response: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "worker_version": request.get("imagen_cli_version"),
        "imagen_cli_version": request.get("imagen_cli_version"),
        "raiw_version": _version(),
    }
    try:
        if operation == "probe":
            response.update(_probe())
        elif operation in {"watermark.batch", "watermark.video.batch"}:
            response.update(_image_batch(request) if operation == "watermark.batch"
                            else _video_batch(request))
        else:
            source = Path(request["input"])
            params = request.get("params") or {}
            output = Path(request["output"]) if request.get("output") else None
            if operation == "watermark.identify":
                response.update(_identify(source))
            elif operation == "matte":
                assert output is not None
                response.update(_matte(source, output, params))
            elif operation == "watermark.classify":
                response.update(_classify(source, params))
            elif operation == "watermark.video.identify":
                response.update(_video_identify(source, params))
            elif operation == "watermark.video.metadata":
                assert output is not None
                response.update(_video_metadata(source, output))
            elif operation == "watermark.video.visible":
                assert output is not None
                response.update(_video_visible(source, output, params))
            elif operation == "watermark.video.all":
                assert output is not None
                response.update(_video_all(source, output, params))
            elif operation == "watermark.video.invisible":
                assert output is not None
                response.update(_video_invisible(source, output, params))
            elif operation == "watermark.metadata":
                assert output is not None
                response.update(_metadata(source, output))
            elif operation == "watermark.visible":
                assert output is not None
                response.update(_visible(source, output, params))
            elif operation == "watermark.erase":
                assert output is not None
                response.update(_erase(source, output, params))
            elif operation == "watermark.all":
                assert output is not None
                response.update(_all(source, output, params))
            else:
                assert output is not None
                response.update(_invisible(source, output, params))
            if output is not None and output.is_file():
                response["output"] = str(output)
                response["output_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
    except Exception as error:  # noqa: BLE001 - the boundary is structured errors only
        response.update({
            "status": "failed",
            "error": {"type": type(error).__name__, "message": str(error)},
        })
    return response


def main(argv: list[str] | None = None) -> int:
    del argv
    try:
        request = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as error:
        response = {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "error": {"type": "validation_error", "message": f"invalid JSON: {error}"},
        }
    else:
        response = handle(request)
    sys.stdout.write(json.dumps(response, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
