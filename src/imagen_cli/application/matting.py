"""Local alpha matting: pinned ONNX weights, tiled inference, explicit alpha polish.

Every entry is MIT or Apache-2.0 so the default stays redistributable and usable
in a commercial pipeline. Non-commercial weights (for example Bria RMBG-2.0, CC
BY-NC 4.0) are deliberately absent: this module has no allowlist that could be
edited into shipping one by accident.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from PIL import Image, ImageFilter

from imagen_cli.application.model_weights import (
    MAX_WEIGHT_BYTES,
    ensure_weight,
    entry_row,
    weight_path,
    weights_directory,
)

# Input normalisation published with each export. The values are part of the
# model contract: a different mean/std silently shifts every alpha edge.
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)

MANIFEST: dict[str, dict[str, Any]] = {
    "birefnet-lite": {
        "url": (
            "https://huggingface.co/onnx-community/BiRefNet_lite-ONNX/"
            "resolve/main/onnx/model.onnx"
        ),
        "sha256": "5600024376f572a557870a5eb0afb1e5961636bef4e1e22132025467d0f03333",
        "license": "MIT",
        "category": "general",
        "usage": "Photos, products and general subjects; soft edges on hair and fur",
        "source": "https://huggingface.co/onnx-community/BiRefNet_lite-ONNX",
        "note": "BiRefNet_lite by ZhengPeng7, exported to ONNX by the transformers.js community",
        "size": 224005088,
        "input_name": "input_image",
        "input_size": 1024,
        "mean": IMAGENET_MEAN,
        "std": IMAGENET_STD,
        "output": "logits",
    },
}


class Runner(Protocol):
    def __call__(self, tile: Image.Image) -> np.ndarray[Any, Any]: ...


_RUNTIME_MISSING = (
    "local matting needs the ai-onnx extra or a local runtime; run: "
    "imagen matting install --backend cpu --accept"
)


def _numpy() -> Any:
    try:
        import numpy
    except ImportError as error:  # pragma: no cover - depends on the extra
        raise ValueError(_RUNTIME_MISSING) from error
    return numpy


def available_providers() -> list[str]:
    try:
        import onnxruntime
    except ImportError:
        return []
    from imagen_cli.application.superres import _preload_providers

    _preload_providers()
    return list(onnxruntime.get_available_providers())


def _provider_chain(backend: str) -> list[str]:
    from imagen_cli.application.superres import PROVIDERS

    if backend not in PROVIDERS:
        raise ValueError("unknown backend: " + backend)
    return PROVIDERS[backend][1]


def _choose_providers(backend: str | None) -> list[str]:
    from imagen_cli.application.superres import AUTO_ORDER, PROVIDERS

    providers = available_providers()
    if not providers:
        raise ValueError(_RUNTIME_MISSING)
    if backend is not None:
        name, _chain = PROVIDERS.get(backend, (None, None))
        if name is None:
            raise ValueError("unknown backend: " + backend)
        if name not in providers:
            raise ValueError(
                f"backend {backend} is unavailable; available: " + ", ".join(sorted(providers))
            )
        return _provider_chain(backend)
    for candidate in AUTO_ORDER:
        name, chain = PROVIDERS[candidate]
        if name in providers:
            return chain
    return ["CPUExecutionProvider"]


def weights_path(model: str, suffix: str = ".onnx") -> Path:
    if model not in MANIFEST:
        raise ValueError("unknown matting model")
    return weight_path(model, suffix)


def ensure_weights(model: str, *, accept: bool = False,
                   download_fn: Callable[[str, Path], None] | None = None) -> Path:
    if model not in MANIFEST:
        raise ValueError("unknown matting model")
    entry = MANIFEST[model]
    return ensure_weight(
        model,
        entry.get("sha256"),
        entry.get("url"),
        accept=accept,
        download_fn=download_fn,
        label="ONNX matting",
        max_bytes=MAX_WEIGHT_BYTES,
    )


def _preflight(data: bytes) -> None:
    """Reuse the super-resolution ONNX policy gate before any ORT session."""
    from imagen_cli.application.superres import _preflight_onnx

    _preflight_onnx(data)


def _require_provider(session: Any, backend: str | None) -> None:
    """Refuse a backend the session fell back from, naming the fix."""
    from imagen_cli.application.superres import ProviderUnavailable
    from imagen_cli.application.superres import _require_provider as check

    try:
        check(session, backend)
    except ProviderUnavailable as error:
        raise ValueError(
            f"{error}; run: imagen matting install --backend {backend} --accept"
        ) from error


def _make_runner(
    model: str, *, backend: str | None, accept: bool,
    session_factory: Callable[..., Any] | None = None,
    precision: str | None = None,
) -> tuple[Runner, dict[str, Any]]:
    numpy = _numpy()
    try:
        import onnxruntime
    except ImportError as error:  # pragma: no cover - depends on the extra
        raise ValueError(_RUNTIME_MISSING) from error
    providers = _choose_providers(backend)
    if precision == "fp16":
        from imagen_cli.application import matting_fp16

        # The derivation verifies the pinned fp32 digest before it writes anything.
        path = matting_fp16.ensure_fp16(model)[0]
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
    else:
        path = ensure_weights(model, accept=accept)
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        pinned = MANIFEST[model].get("sha256")
        if pinned is not None and digest != pinned:
            raise ValueError("weights digest mismatch")
    _preflight(data)
    make_session = session_factory or onnxruntime.InferenceSession
    session = make_session(data, providers=providers)
    if backend is not None:
        _require_provider(session, backend)
    inputs = session.get_inputs()
    if len(inputs) != 1:
        raise ValueError("matting model must take one input")
    name = inputs[0].name
    entry = MANIFEST[model]
    side = int(entry["input_size"])
    mean = numpy.asarray(entry["mean"], dtype=numpy.float32)
    std = numpy.asarray(entry["std"], dtype=numpy.float32)

    def run(tile: Image.Image) -> Any:
        resized = tile.convert("RGB").resize((side, side), Image.Resampling.BICUBIC)
        array = numpy.asarray(resized, dtype=numpy.float32) / 255.0
        array = (array - mean) / std
        chw = numpy.transpose(array, (2, 0, 1))[None].astype(numpy.float32)
        from imagen_cli.application.superres import session_run

        output = session_run(session, {name: chw}, backend)
        if not numpy.isfinite(output).all():
            raise ValueError("model output must contain only finite values")
        logits = numpy.asarray(output)
        if logits.ndim == 4:
            logits = logits[0, 0]
        elif logits.ndim == 3:
            logits = logits[0]
        if logits.ndim != 2:
            raise ValueError("matting model output must be a single-channel map")
        return _sigmoid(logits)

    actual_provider = session.get_providers()[0]
    engine = {
        "backend": backend or actual_provider.replace("ExecutionProvider", "").lower(),
        "provider": actual_provider,
        "dtype": "fp16" if precision == "fp16" else "fp32",
        "precision": "fp16" if precision == "fp16" else "fp32",
        "weights_sha256": digest,
        "input_size": side,
    }
    return run, engine


def _sigmoid(values: Any) -> Any:
    numpy = _numpy()
    clipped = numpy.clip(values.astype(numpy.float32), -60.0, 60.0)
    return 1.0 / (1.0 + numpy.exp(-clipped))


def _to_alpha(matte: Any, size: tuple[int, int]) -> Image.Image:
    numpy = _numpy()
    scaled = numpy.clip(matte * 255.0, 0, 255).astype(numpy.uint8)
    image = Image.fromarray(scaled, "L")
    if image.size != size:
        return image.resize(size, Image.Resampling.BILINEAR)
    return image


def _polish(alpha: Image.Image, *, feather: int, edge_shift: float) -> Image.Image:
    """Explicit alpha polish; defaults leave the model output untouched."""
    if feather < 0 or feather > 64:
        raise ValueError("feather must be between 0 and 64")
    if not math.isfinite(edge_shift) or not -0.5 <= edge_shift <= 0.5:
        raise ValueError("edge_shift must be between -0.5 and 0.5")
    if feather == 0 and edge_shift == 0:
        return alpha
    if edge_shift:
        # A shifted ramp moves the apparent boundary without eroding fine strands.
        numpy = _numpy()
        values = numpy.asarray(alpha, dtype=numpy.float32) / 255.0
        values = numpy.clip((values - edge_shift) / max(1e-6, 1 - abs(edge_shift)), 0, 1)
        alpha = Image.fromarray((values * 255).astype(numpy.uint8), "L")
    if feather:
        alpha = alpha.filter(ImageFilter.GaussianBlur(feather))
    return alpha


def matte(
    image: Image.Image,
    *,
    model: str = "birefnet-lite",
    backend: str | None = None,
    tile: int = 0,
    overlap: int = 64,
    feather: int = 0,
    edge_shift: float = 0.0,
    accept: bool = False,
    runner: Runner | None = None,
    progress: Any = None,
    precision: str = "auto",
) -> tuple[Image.Image, dict[str, Any]]:
    """Return an RGBA copy of ``image`` with a predicted alpha channel.

    ``precision`` is ``auto`` (fp32, retrying once in fp16 when the card cannot
    allocate), ``fp32`` (never falls back) or ``fp16`` (derives half precision
    up front). An injected ``runner`` is used exactly as given.
    """
    from imagen_cli.application.progress import current_progress, report

    progress = progress if progress is not None else current_progress.get()
    if model not in MANIFEST:
        raise ValueError("unknown matting model")
    if tile and (tile < 16 or not 0 <= overlap < tile):
        raise ValueError("invalid tile geometry")
    if precision not in {"auto", "fp32", "fp16"}:
        raise ValueError("precision must be auto, fp32 or fp16")
    side = int(MANIFEST[model].get("input_size", 1024))
    if tile <= 0:
        tile = side
    rgb = image.convert("RGB")
    if runner is not None:
        combined = _tiles_to_alpha(runner, rgb, tile, overlap, progress)
        engine: dict[str, Any] = {"precision": "injected"}
    else:
        report(progress, "loading_model", operation=model)
        chosen = None if precision == "auto" else precision
        runner, engine = _make_runner(model, backend=backend, accept=accept,
                                      precision=chosen)
        try:
            combined = _tiles_to_alpha(runner, rgb, tile, overlap, progress)
        except ValueError as error:
            # Only `auto` retries, only on an allocation failure, and only once.
            if chosen is not None or not _is_out_of_memory(error):
                raise
            try:
                runner, engine = _make_runner(model, backend=backend, accept=accept,
                                              precision="fp16")
                combined = _tiles_to_alpha(runner, rgb, tile, overlap, progress)
            except Exception:  # noqa: BLE001 - report the original allocation failure
                raise error from None
    alpha = _to_alpha(combined, rgb.size)
    alpha = _polish(alpha, feather=feather, edge_shift=edge_shift)
    result = rgb.convert("RGBA")
    result.putalpha(alpha)
    engine["tile"] = tile
    engine["overlap"] = overlap
    return result, engine


def _is_out_of_memory(error: BaseException) -> bool:
    from imagen_cli.application.superres import is_out_of_memory

    return is_out_of_memory(error)


def _tiles_to_alpha(runner: Runner, rgb: Image.Image, tile: int, overlap: int,
                    progress: Any) -> Any:
    """Run every tile and blend the pieces through a cosine window."""
    from imagen_cli.application.progress import report

    numpy = _numpy()
    height, width = rgb.size[1], rgb.size[0]
    accumulator = numpy.zeros((height, width), dtype=numpy.float32)
    weight = numpy.zeros((height, width), dtype=numpy.float32)
    tiles = [
        (left, top, min(width, left + tile), min(height, top + tile))
        for top in range(0, height, tile)
        for left in range(0, width, tile)
    ]
    report(progress, "processing", total=len(tiles), unit="tiles", operation="matte")
    for index, (left, top, right, bottom) in enumerate(tiles, start=1):
        x0, y0 = max(0, left - overlap), max(0, top - overlap)
        x1, y1 = min(width, right + overlap), min(height, bottom + overlap)
        piece = runner(rgb.crop((x0, y0, x1, y1)))
        expected_size = (x1 - x0, y1 - y0)
        matte_tile = numpy.asarray(
            Image.fromarray((numpy.clip(piece, 0, 1) * 255).astype(numpy.uint8), "L")
            .resize(expected_size, Image.Resampling.BILINEAR),
            dtype=numpy.float32,
        ) / 255.0
        # A cosine ramp keeps tile borders from showing as seams.
        mask = _cosine_window(expected_size[0], expected_size[1], overlap)
        inner = matte_tile[top - y0:bottom - y0, left - x0:right - x0]
        inner_mask = mask[top - y0:bottom - y0, left - x0:right - x0]
        accumulator[top:bottom, left:right] += inner * inner_mask
        weight[top:bottom, left:right] += inner_mask
        report(progress, "processing", completed=index, total=len(tiles),
               unit="tiles", operation="matte")
    numpy.maximum(weight, 1e-6, out=weight)
    return numpy.clip(accumulator / weight, 0, 1)


def _cosine_window(width: int, height: int, overlap: int) -> Any:
    numpy = _numpy()
    if overlap <= 0:
        return numpy.ones((height, width), dtype=numpy.float32)
    ramp_x = numpy.ones(width, dtype=numpy.float32)
    ramp_y = numpy.ones(height, dtype=numpy.float32)
    span = min(overlap, width, height)
    if span > 0:
        ramp = 0.5 - 0.5 * numpy.cos(numpy.linspace(0, math.pi, span, dtype=numpy.float32))
        ramp_x[:span] = ramp
        ramp_x[-span:] = ramp[::-1]
        ramp_y[:span] = ramp
        ramp_y[-span:] = ramp[::-1]
    return numpy.outer(ramp_y, ramp_x)


def op(image: Image.Image, *, model: str = "birefnet-lite", backend: str | None = None,
       tile: int = 0, overlap: int = 64, feather: int = 0, edge_shift: float = 0.0,
       accept: bool = False) -> Image.Image:
    return matte(
        image, model=model, backend=backend, tile=tile, overlap=overlap,
        feather=feather, edge_shift=edge_shift, accept=accept,
    )[0]


def backend_report() -> dict[str, Any]:
    """Runtime facts for this family only: the shared reporter names every manifest."""
    from imagen_cli.application.superres import _runtime_report

    providers = available_providers()
    return {
        "onnxruntime": bool(providers),
        "providers": providers,
        "runtime": _runtime_report(),
        "model_names": sorted(MANIFEST),
        "model_dir": str(weights_directory()),
    }


def catalog(*, verify: bool = False) -> dict[str, Any]:
    models = []
    for name, entry in sorted(MANIFEST.items()):
        row = entry_row(name, entry, verify=verify)
        row["task"] = "matting"
        # Matting keeps the source dimensions; the column exists for both families.
        row["scale"] = 1
        models.append(row)
    return {**backend_report(), "models": models, "verified": verify}


def fetch_choices() -> list[tuple[str, str]]:
    return [
        (f"{name}: {entry['license']} | {entry['source']}", name)
        for name, entry in MANIFEST.items()
    ]


def fetch(model: str, *, accept: bool = False) -> dict[str, Any]:
    path = ensure_weights(model, accept=accept)
    return {
        "model": model,
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def install(
    models: str | list[str] | None = None,
    *,
    backend: str = "cpu",
    accept: bool = False,
    runtime_install: Callable[..., tuple[str, dict[str, Any]]] | None = None,
    download_fn: Callable[[str, Path], None] | None = None,
    provider_probe: Callable[[], list[str]] | None = None,
) -> dict[str, Any]:
    """Preview or install the runtime and requested matting weights."""
    from imagen_cli.application.superres import _backend_provider, _subprocess_providers

    names = [] if models is None else ([models] if isinstance(models, str) else list(models))
    if len(set(names)) != len(names):
        raise ValueError("install needs distinct model names")
    for name in names:
        if name not in MANIFEST:
            raise ValueError("unknown matting model")
    provider = _backend_provider(backend)
    runtime_ready = provider in available_providers()
    runtime_data: dict[str, Any] | None = None
    if accept and not runtime_ready:
        if runtime_install is None:
            from imagen_cli.application import runtime_service

            runtime_install = runtime_service.install
        _, runtime_data = runtime_install(backend=backend, accept=True)
        runtime_ready = provider in (provider_probe or _subprocess_providers)()
    rows = []
    for name in names:
        entry = MANIFEST[name]
        path = weights_path(name)
        row: dict[str, Any] = {
            "model": name,
            "task": "matting",
            "category": entry.get("category", "general"),
            "license": entry["license"],
            "source": entry["source"],
            "target": str(path),
            "downloadable": entry.get("url") is not None,
            "downloaded": path.exists(),
        }
        if accept:
            path = ensure_weights(name, accept=True, download_fn=download_fn)
            row.update(
                downloaded=True,
                path=str(path),
                bytes=path.stat().st_size,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        rows.append(row)
    installed = runtime_ready and all(row["downloaded"] for row in rows)
    data: dict[str, Any] = {
        "backend": backend,
        "runtime_installed": runtime_ready,
        "models": rows,
        "installed": installed,
    }
    if runtime_data is not None:
        data["runtime"] = runtime_data
    if not accept:
        data["hint"] = (
            "Review each licence and source, then rerun with --accept to install the "
            "runtime and download the selected weights."
            if names
            else "No model selected: --accept installs only the runtime. "
            "Add --model NAME (see: imagen matting models) to include weights."
        )
    return data


def doctor(model: str | None = None, *, backend: str = "auto") -> dict[str, Any]:
    """Prove the requested backend with one tile; never downloads or saves an artifact."""
    from imagen_cli.application.superres import (
        _backend_provider,
        _doctor_backend,
        _runtime_report,
    )

    providers = available_providers()
    used, fallback = _doctor_backend(backend, providers)
    data: dict[str, Any] = {
        "providers": providers,
        "requested_backend": backend,
        "backend": used,
        "provider_used": _backend_provider(used) if used else None,
        "fallback": fallback,
        "runtime": _runtime_report(),
        "model": None,
        "weights_verified": False,
        "inference": False,
        "precision_used": None,
        "fp16_cached": False,
        "latency_ms": None,
        "output_sha256": None,
        "status": "ready" if used else "missing",
        "missing": [],
    }
    if used is None:
        if backend != "auto":
            data["missing"] = [_backend_provider(backend)]
            data["hint"] = (
                "CUDA needs the onnxruntime-gpu build plus CUDA 12.x and cuDNN 9 on the "
                "loader path; run: imagen matting install --backend cuda --accept"
                if backend == "cuda"
                else f"run: imagen matting install --backend {backend} --accept"
            )
        else:
            data["hint"] = (
                "no ONNX provider is available; run: imagen matting install --backend cpu --accept"
            )
        return data
    from imagen_cli.application import matting_fp16

    selected = model
    if selected is None:
        for name in sorted(MANIFEST):
            if weights_path(name).is_file():
                selected = name
                break
    elif selected not in MANIFEST:
        raise ValueError("unknown matting model")
    data["model"] = selected
    data["fp16_cached"] = bool(selected) and matting_fp16.derived_path(
        str(selected)
    ).is_file()
    if selected is None:
        data["hint"] = "no downloaded weights; run: imagen matting install --model NAME --accept"
        return data
    import time

    started = time.monotonic()
    try:
        _output, engine = matte(
            Image.new("RGB", (64, 64), (127, 127, 127)),
            model=selected, backend=used, tile=64, overlap=0, accept=False,
        )
    except ValueError as error:
        # A provider can be listed and still fail to load; report that instead of
        # raising, because the doctor's job is to name the broken layer.
        from imagen_cli.application.superres import ProviderUnavailable

        if not isinstance(error, ProviderUnavailable):
            raise
        data["status"] = "missing"
        data["backend"] = None
        data["provider_used"] = None
        data["missing"] = [_backend_provider(used)]
        data["hint"] = (
            str(error) + "; on a small card try --precision fp16, which derives a "
            "half-precision copy, or use the cpu backend"
            if _is_out_of_memory(error) else str(error)
        )
        return data
    data["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
    data["weights_verified"] = True
    data["inference"] = True
    data["precision_used"] = engine.get("precision")
    data["provider_used"] = engine.get("provider", data["provider_used"])
    data["weights_sha256"] = engine.get("weights_sha256")
    data["output_sha256"] = hashlib.sha256(_output.tobytes()).hexdigest()
    return data


OPS = {"matte": op}
