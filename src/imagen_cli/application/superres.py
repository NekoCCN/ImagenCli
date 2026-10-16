"""Optional ONNX super-resolution: tiled, backend explicit, weights never bundled."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import httpx
from PIL import Image

# Upstream publishes .pth checkpoints, not ONNX. No URL is claimed here: users
# supply an ONNX export and its digest is recorded on first use (trust on first use).
MANIFEST: dict[str, dict[str, Any]] = {
    "realesrgan-x4plus": {
        "url": None, "scale": 4, "license": "BSD-3-Clause",
        "source": "https://github.com/xinntao/Real-ESRGAN",
    },
    "realesrgan-x4plus-anime": {
        "url": None, "scale": 4, "license": "BSD-3-Clause",
        "source": "https://github.com/xinntao/Real-ESRGAN",
    },
    "realesrgan-x4v3": {
        "url": None, "scale": 4, "license": "BSD-3-Clause",
        "source": "https://github.com/xinntao/Real-ESRGAN",
    },
    "span-x4": {
        "url": None, "scale": 4, "license": "Apache-2.0",
        "source": "https://github.com/hongyuanyu/SPAN",
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


def _download(url: str, target: Path) -> None:
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
    if path.exists():
        recorded = digest_path(model)
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if recorded.exists():
            if recorded.read_text().strip() != actual:
                raise ValueError("weights digest mismatch")
        else:
            recorded.write_text(actual)
        return path
    url = MANIFEST[model].get("url")
    if url is None:
        raise ValueError(
            f"weights are missing; place {path.name} in {path.parent} "
            "(no upstream ONNX release is claimed)"
        )
    if not accept:
        raise ValueError(
            "weights are missing; rerun with accept=true after reviewing " + str(url)
        )
    (download or _download)(str(url), path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    digest_path(model, suffix).write_text(digest)
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
        session = torch.jit.load(str(path), map_location=target)
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

    def run(tile: Any) -> Any:
        array = numpy.asarray(tile, dtype=numpy.float32) / 255.0
        chw = numpy.transpose(array, (2, 0, 1))[None]
        output = session.run(None, {name: chw})[0]
        result = numpy.squeeze(output, axis=0)
        result = numpy.transpose(result, (1, 2, 0))
        return numpy.clip(result, 0, 1) * 255

    engine = {
        "backend": backend or providers[0].replace("ExecutionProvider", "").lower(),
        "provider": session.get_providers()[0],
        "device": device or "auto",
        "dtype": "fp32",
        "weights_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    return run, engine


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


def backend_report() -> dict[str, Any]:
    providers = available_providers()
    return {
        "onnxruntime": bool(providers),
        "providers": providers,
        "models": sorted(MANIFEST),
        "model_dir": str(weights_directory()),
    }


OPS = {"superres": op}
