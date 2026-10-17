"""Optional ONNX super-resolution: tiled, backend explicit, weights never bundled."""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from PIL import Image

# The x2/x4 entries are pinned exports of the canonical Real-ESRGAN weights; the
# anime entry has no maintained ONNX release, so users place it themselves.
MANIFEST: dict[str, dict[str, Any]] = {
    "realesrgan-x4plus": {
        "url": "https://huggingface.co/SceneWorks/real-esrgan-onnx/resolve/main/real_esrgan_x4.onnx",
        "sha256": "5c586662929cbc686c1a5c38d9c060dbdb4ea5863a1f7672b8c0761e6b89c033",
        "scale": 4, "license": "BSD-3-Clause",
        "source": "https://huggingface.co/SceneWorks/real-esrgan-onnx",
    },
    "realesrgan-x2plus": {
        "url": "https://huggingface.co/SceneWorks/real-esrgan-onnx/resolve/main/real_esrgan_x2.onnx",
        "sha256": "7115ba92e8a1bfa63d68558ef006ef3d91273a068d321b1439f8bb1c9179002c",
        "scale": 2, "license": "BSD-3-Clause",
        "source": "https://huggingface.co/SceneWorks/real-esrgan-onnx",
    },
    "realesrgan-x4plus-anime": {
        "url": "https://huggingface.co/xiongjie/lightweight-real-ESRGAN-anime/resolve/main/RealESRGAN_x4plus_anime_4B32F.onnx",
        "sha256": "2208c7ae8db793330abf1248fbce15585ad317e921c456265572836b92926c9a",
        "scale": 4, "license": "BSD-3-Clause (upstream weights)",
        "source": "https://huggingface.co/xiongjie/lightweight-real-ESRGAN-anime",
        "note": "community ONNX export of RealESRGAN_x4plus_anime_6B",
    },
    "swinir-s-x2": {
        "url": "https://huggingface.co/notaneimu/onnx-image-models/resolve/main/002_lightweightSR_DIV2K_s64w8_SwinIR-S_x2.onnx",
        "sha256": "33706e22f983dfe2a16d10482392a957b22e342d106adf1a7886ef161903540a",
        "scale": 2, "license": "Apache-2.0 (upstream); export repo tagged other",
        "source": "https://huggingface.co/notaneimu/onnx-image-models",
        "multiple": 8, "fixed_input": 64,
    },
    "swinir-s-x4": {
        "url": "https://huggingface.co/notaneimu/onnx-image-models/resolve/main/002_lightweightSR_DIV2K_s64w8_SwinIR-S_x4.onnx",
        "sha256": "052d17a1ba08fa625f64305c9bf7e9ea5cf738e8fcd3a4e0b8160bce1c4ee7c2",
        "scale": 4, "license": "Apache-2.0 (upstream); export repo tagged other",
        "source": "https://huggingface.co/notaneimu/onnx-image-models",
        "multiple": 8, "fixed_input": 64,
    },
}

PROVIDERS: dict[str, tuple[str, list[str]]] = {
    "cuda": ("CUDAExecutionProvider", ["CUDAExecutionProvider", "CPUExecutionProvider"]),
    "coreml": ("CoreMLExecutionProvider", ["CoreMLExecutionProvider", "CPUExecutionProvider"]),
    "dml": ("DmlExecutionProvider", ["DmlExecutionProvider", "CPUExecutionProvider"]),
    "openvino": ("OpenVINOExecutionProvider",
                 ["OpenVINOExecutionProvider", "CPUExecutionProvider"]),
    "cpu": ("CPUExecutionProvider", ["CPUExecutionProvider"]),
}
AUTO_ORDER = ("cuda", "coreml", "dml", "openvino", "cpu")
MAX_WEIGHT_BYTES = 2 * 1024 * 1024 * 1024


class Runner(Protocol):
    def __call__(self, tile: Any) -> Any: ...


def _numpy() -> Any:
    try:
        import numpy
    except ImportError as error:
        raise ValueError("super-resolution requires the ai-onnx extra") from error
    return numpy


def available_providers() -> list[str]:
    try:
        import onnxruntime
    except ImportError:
        return []
    return list(onnxruntime.get_available_providers())


def _choose_provider(backend: str | None, device: str | None) -> list[str]:
    providers = available_providers()
    if not providers:
        raise ValueError("super-resolution requires the ai-onnx extra")
    requested = backend or device
    if requested is not None:
        if requested not in PROVIDERS:
            raise ValueError("unknown backend: " + requested)
        name, chain = PROVIDERS[requested]
        if name not in providers:
            raise ValueError(
                f"backend {requested} is unavailable; available: "
                + ", ".join(sorted(providers))
            )
        return chain
    for candidate in AUTO_ORDER:
        name, chain = PROVIDERS[candidate]
        if name in providers:
            return chain
    return ["CPUExecutionProvider"]


def weights_directory() -> Path:
    configured = os.environ.get("IMAGEN_MODEL_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".cache" / "imagen" / "models"


def weights_path(model: str, suffix: str = ".onnx") -> Path:
    if model not in MANIFEST:
        raise ValueError("unknown super-resolution model")
    if suffix not in {".onnx", ".pt"}:
        raise ValueError("weights suffix must be .onnx or .pt")
    return weights_directory() / f"{model}{suffix}"


def digest_path(model: str, suffix: str = ".onnx") -> Path:
    return weights_path(model, suffix).with_suffix(".sha256")


def _recorded_digest(model: str, suffix: str) -> str | None:
    path = digest_path(model, suffix)
    return path.read_text(encoding="ascii").strip() if path.exists() else None


def _record_digest(model: str, suffix: str, digest: str) -> None:
    digest_path(model, suffix).write_text(digest)


def _download(url: str, target: Path) -> None:
    import httpx

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".part")
    if target.exists():
        return
    with httpx.stream(
        "GET", url, timeout=120.0, follow_redirects=True, trust_env=False
    ) as response:
        response.raise_for_status()
        if response.headers.get("content-length") and int(
            response.headers["content-length"]
        ) > MAX_WEIGHT_BYTES:
            raise ValueError("weights exceed the size limit")
        with temporary.open("wb") as output:
            for chunk in response.iter_bytes():
                output.write(chunk)
                if output.tell() > MAX_WEIGHT_BYTES:
                    raise ValueError("weights exceed the size limit")
    temporary.replace(target)


def ensure_weights(model: str, *, accept: bool = False, suffix: str = ".onnx",
                   download: Callable[[str, Path], None] | None = None) -> Path:
    path = weights_path(model, suffix)
    pinned = MANIFEST[model].get("sha256") if suffix == ".onnx" else None
    if path.exists():
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        expected = pinned or _recorded_digest(model, suffix)
        if expected is not None and expected != actual:
            raise ValueError("weights digest mismatch")
        if expected is None:
            _record_digest(model, suffix, actual)
        return path
    url = MANIFEST[model].get("url")
    if url is None:
        raise ValueError(
            f"weights are missing; place {path.name} in {path.parent} "
            "(no maintained ONNX release is claimed)"
        )
    if not accept:
        raise ValueError(
            "weights are missing; rerun with accept=true after reviewing " + str(url)
        )
    (download or _download)(str(url), path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if pinned is not None and digest != pinned:
        path.unlink(missing_ok=True)
        raise ValueError("downloaded weights do not match the pinned digest")
    _record_digest(model, suffix, digest)
    return path


def _make_torch_runner(model: str, *, device: str | None,
                       accept: bool) -> tuple[Runner, dict[str, Any]]:
    numpy = _numpy()
    try:
        import torch
    except ImportError as error:
        raise ValueError("the torch backend requires the ai-torch extra") from error
    path = ensure_weights(model, accept=accept, suffix=".pt")
    target = device or ("cuda" if torch.cuda.is_available() else "cpu")
    try:
        session = torch.jit.load(  # type: ignore[no-untyped-call, unused-ignore]
            str(path), map_location=target
        )
    except Exception as error:  # noqa: BLE001 - torch raises backend-specific errors
        raise ValueError("weights are not a loadable TorchScript model") from error

    def run(tile: Any) -> Any:
        tensor = torch.from_numpy(
            numpy.asarray(tile, dtype=numpy.float32) / 255.0
        ).permute(2, 0, 1)[None].to(target)
        with torch.no_grad():
            output = session(tensor)
        result = output.clamp(0, 1)[0].permute(1, 2, 0).cpu().numpy()
        return result * 255

    engine = {
        "backend": "torch", "provider": "torch", "device": target, "dtype": "fp32",
        "weights_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    return run, engine


def _make_runner(model: str, *, backend: str | None, device: str | None,
                 accept: bool) -> tuple[Runner, dict[str, Any]]:
    if backend == "torch":
        return _make_torch_runner(model, device=device, accept=accept)
    numpy = _numpy()
    try:
        import onnxruntime
    except ImportError as error:  # pragma: no cover - depends on the extra
        raise ValueError("super-resolution requires the ai-onnx extra") from error
    providers = _choose_provider(backend, device)
    path = ensure_weights(model, accept=accept)
    session = onnxruntime.InferenceSession(str(path), providers=providers)
    inputs = session.get_inputs()
    if len(inputs) != 1:
        raise ValueError("super-resolution model must take one input")
    name = inputs[0].name
    shape = list(inputs[0].shape)
    fixed_height = shape[2] if len(shape) == 4 and isinstance(shape[2], int) else None
    fixed_width = shape[3] if len(shape) == 4 and isinstance(shape[3], int) else None
    fixed_input = MANIFEST[model].get("fixed_input")
    fixed_height = fixed_height or fixed_input
    fixed_width = fixed_width or fixed_input
    multiple = int(MANIFEST[model].get("multiple", 1))

    def run(tile: Any) -> Any:
        array = numpy.asarray(tile, dtype=numpy.float32)
        height, width = array.shape[:2]
        target_height = fixed_height or _round_up(height, multiple)
        target_width = fixed_width or _round_up(width, multiple)
        if (target_height, target_width) != (height, width):
            array = numpy.pad(
                array,
                ((0, target_height - height), (0, target_width - width), (0, 0)),
                mode="edge",
            )
        chw = numpy.transpose(array / 255.0, (2, 0, 1))[None]
        output = session.run(  # type: ignore[no-untyped-call, unused-ignore]
            None, {name: chw}
        )[0]
        result = numpy.squeeze(output, axis=0)
        result = numpy.transpose(result, (1, 2, 0))
        scale = int(MANIFEST[model]["scale"])
        return numpy.clip(result, 0, 1)[: height * scale, : width * scale] * 255

    engine = {
        "backend": backend or providers[0].replace("ExecutionProvider", "").lower(),
        "provider": session.get_providers()[0],
        "device": device or "auto",
        "dtype": "fp32",
        "weights_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    return run, engine


def _round_up(value: int, multiple: int) -> int:
    if multiple <= 1:
        return value
    return ((value + multiple - 1) // multiple) * multiple


def tiled_upscale(array: Any, runner: Runner, *, scale: int, tile: int = 256,
                  overlap: int = 16) -> Any:
    """Run a tile-wise upscaler and average overlapping predictions."""
    numpy = _numpy()
    if tile < 4 or not 0 <= overlap < tile:
        raise ValueError("invalid tile geometry")
    height, width = array.shape[:2]
    output = numpy.zeros((height * scale, width * scale, 3), dtype=numpy.float64)
    weights = numpy.zeros((height * scale, width * scale, 1), dtype=numpy.float64)
    for top in range(0, height, tile):
        for left in range(0, width, tile):
            bottom = min(height, top + tile)
            right = min(width, left + tile)
            y0, x0 = max(0, top - overlap), max(0, left - overlap)
            y1, x1 = min(height, bottom + overlap), min(width, right + overlap)
            piece = runner(array[y0:y1, x0:x1])
            expected = ((y1 - y0) * scale, (x1 - x0) * scale, 3)
            if piece.shape != expected:
                raise ValueError("model output shape does not match its scale")
            area = (
                slice(top * scale - y0 * scale, bottom * scale - y0 * scale),
                slice(left * scale - x0 * scale, right * scale - x0 * scale),
            )
            destination = (
                slice(top * scale, bottom * scale),
                slice(left * scale, right * scale),
            )
            output[destination] += piece[area]
            weights[destination] += 1
    return numpy.clip(output / numpy.maximum(weights, 1), 0, 255)


def upscale(image: Image.Image, *, model: str = "realesrgan-x4plus",
            backend: str | None = None, device: str | None = None,
            tile: int = 256, overlap: int = 16, accept: bool = False,
            runner: Runner | None = None) -> tuple[Image.Image, dict[str, Any]]:
    numpy = _numpy()
    if model not in MANIFEST:
        raise ValueError("unknown super-resolution model")
    scale = int(MANIFEST[model]["scale"])
    fixed_input = MANIFEST[model].get("fixed_input")
    if fixed_input:
        # Some exports only accept one square size; overlap would exceed it.
        tile, overlap = int(fixed_input), 0
    engine: dict[str, Any] = {}
    if runner is None:
        runner, engine = _make_runner(model, backend=backend, device=device, accept=accept)
    alpha = image.getchannel("A") if "A" in image.getbands() else None
    array = numpy.asarray(image.convert("RGB"), dtype=numpy.uint8)
    result = tiled_upscale(array, runner, scale=scale, tile=tile, overlap=overlap)
    converted = Image.fromarray(result.astype(numpy.uint8), "RGB")
    if alpha is not None:
        converted = converted.convert("RGBA")
        enlarged = alpha.resize(
            (alpha.width * scale, alpha.height * scale), Image.Resampling.LANCZOS
        )
        converted.putalpha(enlarged)
    engine["tile"] = tile
    engine["overlap"] = overlap
    return converted, engine


def op(image: Image.Image, *, model: str = "realesrgan-x4plus",
       backend: str | None = None, device: str | None = None, tile: int = 256,
       overlap: int = 16, accept: bool = False) -> Image.Image:
    return upscale(
        image, model=model, backend=backend, device=device, tile=tile,
        overlap=overlap, accept=accept,
    )[0]


def catalog() -> dict[str, Any]:
    models = []
    for name, entry in sorted(MANIFEST.items()):
        path = weights_path(name)
        models.append({
            "model": name,
            "scale": entry["scale"],
            "license": entry["license"],
            "source": entry["source"],
            "note": entry.get("note"),
            "downloadable": entry.get("url") is not None,
            "downloaded": path.exists(),
            "bytes": path.stat().st_size if path.exists() else None,
            "sha256": entry.get("sha256"),
            "multiple": entry.get("multiple", 1),
        })
    return {**backend_report(), "models": models}


def fetch(model: str, *, accept: bool = False) -> dict[str, Any]:
    path = ensure_weights(model, accept=accept)
    return {
        "model": model,
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def benchmark(
    repositories: Any,
    store: Any,
    artifact: str,
    models: list[str],
    *,
    backend: str | None = None,
    tile: int = 256,
    overlap: int = 16,
) -> dict[str, Any]:
    """Self-consistency check: downscale, upscale back, compare with the original."""
    from imagen_cli.application import filters_numpy

    if not 1 <= len(models) <= 8 or len(set(models)) != len(models):
        raise ValueError("benchmark needs one to eight distinct models")
    for model in models:
        if model not in MANIFEST:
            raise ValueError("unknown super-resolution model")
    row = repositories.get("artifacts", artifact)
    if row is None or row["save_state"] != "published":
        raise ValueError("artifact must be published")
    path = store.get(artifact)
    if path is None:
        raise ValueError("artifact bytes are unavailable")
    with Image.open(path) as opened:
        opened.load()
        reference = opened.convert("RGB")
    results = []
    for model in models:
        scale = int(MANIFEST[model]["scale"])
        small = reference.resize(
            (max(1, reference.width // scale), max(1, reference.height // scale)),
            Image.Resampling.LANCZOS,
        )
        started = time.perf_counter()
        output, engine = upscale(
            small, model=model, backend=backend, tile=tile, overlap=overlap,
        )
        elapsed = round((time.perf_counter() - started) * 1000)
        measured = output
        if output.size != reference.size:
            measured = output.resize(reference.size, Image.Resampling.LANCZOS)
        results.append({
            "model": model,
            "scale": scale,
            "width": output.width,
            "height": output.height,
            "input": [small.width, small.height],
            "psnr": round(filters_numpy.psnr(reference, measured), 4),
            "ssim": round(filters_numpy.ssim(reference, measured), 6),
            "duration_ms": elapsed,
            "engine": engine,
        })
    return {
        "artifact": artifact,
        "reference": [reference.width, reference.height],
        "results": results,
    }


def backend_report() -> dict[str, Any]:
    providers = available_providers()
    return {
        "onnxruntime": bool(providers),
        "providers": providers,
        "model_names": sorted(MANIFEST),
        "model_dir": str(weights_directory()),
    }


OPS = {"superres": op}
