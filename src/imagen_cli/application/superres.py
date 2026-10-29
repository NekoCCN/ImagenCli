"""Optional ONNX super-resolution: tiled, backend explicit, weights never bundled."""

from __future__ import annotations

import hashlib
import io
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from PIL import Image

# Every downloadable entry is pinned to a SHA-256. Community ONNX conversions
# are labelled as such; entries without a URL require the user to place weights.
MANIFEST: dict[str, dict[str, Any]] = {
    "film-degrainer": {
        "url": "https://huggingface.co/notaneimu/onnx-image-models/resolve/main/1x-Film-Degrainer-1-000.onnx",
        "sha256": "8dae380741328caf5bf3abe29636732dc2db9b7e7a9a812d7314279cc7e682fa",
        "scale": 1, "task": "denoise", "license": "CC0-1.0",
        "category": "restoration",
        "usage": "Film and cel stills with visible grain or sensor noise",
        "source": "https://openmodeldb.info/models/1x-Film-Degrainer-1-000",
        "note": "Film-Degrainer by tika; community ONNX conversion by notaneimu",
    },
    "dejpg-srformer-light": {
        "url": "https://huggingface.co/notaneimu/onnx-image-models/resolve/main/1xDeJPG_SRFormer_light_fp32_64.onnx",
        "sha256": "63e85f102025f024aa92eb2d9a73851b5c3a342e6aedebd02f2bff985ef3bd3b",
        "scale": 1, "task": "dejpeg", "multiple": 8, "fixed_input": 64,
        "license": "CC-BY-4.0",
        "category": "restoration",
        "usage": "Compressed anime stills and screenshots with JPEG blocking",
        "source": "https://openmodeldb.info/models/1x-DeJPG-SRFormer-light",
        "note": "DeJPG_SRFormer_light by Helaman; ONNX conversion by notaneimu; "
                "attribution and indication of changes required",
    },
    "realesrgan-x4plus": {
        "url": "https://huggingface.co/SceneWorks/real-esrgan-onnx/resolve/main/real_esrgan_x4.onnx",
        "sha256": "5c586662929cbc686c1a5c38d9c060dbdb4ea5863a1f7672b8c0761e6b89c033",
        "scale": 4, "license": "BSD-3-Clause",
        "category": "general",
        "usage": "Photos and mixed content",
        "source": "https://huggingface.co/SceneWorks/real-esrgan-onnx",
    },
    "realesrgan-x2plus": {
        "url": "https://huggingface.co/SceneWorks/real-esrgan-onnx/resolve/main/real_esrgan_x2.onnx",
        "sha256": "7115ba92e8a1bfa63d68558ef006ef3d91273a068d321b1439f8bb1c9179002c",
        "scale": 2, "license": "BSD-3-Clause", "multiple": 2,
        "category": "general",
        "usage": "Photos and mixed content at 2x",
        "source": "https://huggingface.co/SceneWorks/real-esrgan-onnx",
    },
    "realesrgan-x4plus-anime": {
        "url": "https://huggingface.co/xiongjie/lightweight-real-ESRGAN-anime/resolve/main/RealESRGAN_x4plus_anime_4B32F.onnx",
        "sha256": "2208c7ae8db793330abf1248fbce15585ad317e921c456265572836b92926c9a",
        "scale": 4, "license": "BSD-3-Clause (upstream weights)",
        "category": "acg",
        "usage": "Anime illustrations and cel-shaded art at 4x",
        "source": "https://huggingface.co/xiongjie/lightweight-real-ESRGAN-anime",
        "note": "community ONNX export of RealESRGAN_x4plus_anime_6B",
    },
    "realesr-animevideov3": {
        "url": "https://huggingface.co/skillsafe-ai/realesr-animevideov3/resolve/main/model.onnx",
        "sha256": "78baa685a1a92cac6e14ab2af2a8ad0ef56fa48629563b1414e3a7acc54d86e2",
        "scale": 4, "license": "BSD-3-Clause",
        "category": "acg",
        "usage": "Cel animation frames and anime stills with flat color areas",
        "source": "https://huggingface.co/skillsafe-ai/realesr-animevideov3",
        "note": "AnimeVideo-v3 by Xintao Wang; reproducible ONNX conversion with pinned "
                "upstream digest and reference tensors",
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


_RUNTIME_MISSING = (
    "super-resolution needs the ai-onnx extra or a local runtime; run: "
    "imagen superres install --backend cpu --accept"
)


def _numpy() -> Any:
    try:
        import numpy
    except ImportError as error:
        raise ValueError(_RUNTIME_MISSING) from error
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
        raise ValueError(_RUNTIME_MISSING)
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
    path = weights_directory() / f"{model}{suffix}"
    for candidate in (path, path.with_suffix(".sha256"), path.with_suffix(".part")):
        if any(part.is_symlink() for part in (candidate, *candidate.parents)):
            raise ValueError("model paths must not contain symlinks")
    return path


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
        if not path.is_file() or path.stat().st_size > MAX_WEIGHT_BYTES:
            raise ValueError("weights exceed the size limit or are not a regular file")
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
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=path.parent, prefix='.imagen-download-') as temporary:
        staged = Path(temporary) / path.name
        (download or _download)(str(url), staged)
        if not staged.is_file() or staged.stat().st_size > MAX_WEIGHT_BYTES:
            raise ValueError('weights exceed the size limit or are not a regular file')
        digest = hashlib.sha256(staged.read_bytes()).hexdigest()
        if pinned is not None and digest != pinned:
            raise ValueError('downloaded weights do not match the pinned digest')
        # Hard-link publication refuses an existing file instead of overwriting it.
        os.link(staged, path)
    _record_digest(model, suffix, digest)
    return path


def _make_torch_runner(model: str, *, device: str | None,
                       accept: bool) -> tuple[Runner, dict[str, Any]]:
    raise ValueError(
        "unsafe local TorchScript loading is disabled, including with ai-torch; "
        "use a pinned ONNX export; no automatic backend substitution"
    )


def _preflight_onnx(data: bytes) -> None:
    """Reject external files and nonstandard semantics before any ORT session.

    This is format/policy validation, not a sandbox or a resource-exhaustion guard.
    Only the canonical standard ONNX domain ("") is accepted, not ai.onnx.ml.
    No custom-op library is registered. SHA pinning remains a separate prior gate.
    Local functions are rejected to avoid overriding standard operator semantics.
    See https://onnx.ai/onnx/repo-docs/ExternalData.html and
    https://onnx.ai/onnx/api/checker.html for loader/checker behavior.
    """
    try:
        import onnx
    except ImportError as error:  # pragma: no cover - depends on the extra
        raise ValueError("ONNX safety preflight requires the ai-onnx extra") from error
    try:
        model = onnx.load_model(io.BytesIO(data), load_external_data=False)
        pending = [model]
        has_local_functions = False
        while pending:
            message = pending.pop()
            kind = message.DESCRIPTOR.full_name
            if kind == "onnx.TensorProto" and (
                message.data_location == 1 or message.external_data
            ):
                raise ValueError("ONNX external data is not allowed")
            if kind in {"onnx.NodeProto", "onnx.FunctionProto", "onnx.OperatorSetIdProto"}:
                if message.domain != "":
                    raise ValueError("nonstandard ONNX domain is not allowed")
                if kind == "onnx.FunctionProto":
                    has_local_functions = True
            if kind == "onnx.NodeProto" and not onnx.defs.has(message.op_type, ""):
                raise ValueError("nonstandard ONNX operator/domain is not allowed")
            if kind == "onnx.OperatorSetIdProto" and not (
                1 <= message.version <= onnx.defs.onnx_opset_version()
            ):
                raise ValueError("unsupported ONNX opset")
            # Walk every nested message. upb repeated containers expose only a
            # sequence protocol (no __iter__), and descriptor labels differ
            # across protobuf versions, so detect containers structurally.
            for _field, value in message.ListFields():
                if hasattr(value, "ListFields"):
                    pending.append(value)
                elif not isinstance(value, (str, bytes)) and hasattr(value, "__len__"):
                    pending.extend(item for item in value if hasattr(item, "ListFields"))
        if has_local_functions:
            raise ValueError("local functions cannot redefine standard ONNX domain semantics")
        onnx.checker.check_model(model)
    except Exception as error:  # noqa: BLE001 - parser/checker have native error types
        raise ValueError(f"ONNX safety preflight failed: {error}") from error


def _make_runner(model: str, *, backend: str | None, device: str | None,
                 accept: bool) -> tuple[Runner, dict[str, Any]]:
    if backend == "torch":
        return _make_torch_runner(model, device=device, accept=accept)
    numpy = _numpy()
    try:
        import onnxruntime
    except ImportError as error:  # pragma: no cover - depends on the extra
        raise ValueError(_RUNTIME_MISSING) from error
    providers = _choose_provider(backend, device)
    path = ensure_weights(model, accept=accept)
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    expected = MANIFEST[model].get("sha256") or _recorded_digest(model, ".onnx")
    if expected is not None and digest != expected:
        raise ValueError("weights digest mismatch")
    _preflight_onnx(data)
    # Use the checked bytes, not a path that could change after preflight.
    session = onnxruntime.InferenceSession(data, providers=providers)
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
        if not numpy.isfinite(output).all():
            raise ValueError("model output must contain only finite values")
        result = numpy.squeeze(output, axis=0)
        result = numpy.transpose(result, (1, 2, 0))
        scale = int(MANIFEST[model]["scale"])
        return numpy.clip(result, 0, 1)[: height * scale, : width * scale] * 255

    engine = {
        "backend": backend or providers[0].replace("ExecutionProvider", "").lower(),
        "provider": session.get_providers()[0],
        "device": device or "auto",
        "dtype": "fp32",
        "weights_sha256": digest,
    }
    return run, engine


def _round_up(value: int, multiple: int) -> int:
    if multiple <= 1:
        return value
    return ((value + multiple - 1) // multiple) * multiple


def tiled_upscale(array: Any, runner: Runner, *, scale: int, tile: int = 256,
                  overlap: int = 16, progress: Any = None) -> Any:
    """Run overlapping tiles and write their disjoint cores into one output buffer."""
    numpy = _numpy()
    if tile < 4 or not 0 <= overlap < tile:
        raise ValueError("invalid tile geometry")
    height, width = array.shape[:2]
    # uint8 is the exact output range after the clip below, and a quarter of float32.
    output = numpy.empty((height * scale, width * scale, 3), dtype=numpy.uint8)
    from imagen_cli.application.progress import report

    total = ((height + tile - 1) // tile) * ((width + tile - 1) // tile)
    completed = 0
    report(progress, "processing", total=total, unit="tiles", operation="superres")
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
            if not numpy.isfinite(piece).all():
                raise ValueError("model output must contain only finite values")
            area = (
                slice(top * scale - y0 * scale, bottom * scale - y0 * scale),
                slice(left * scale - x0 * scale, right * scale - x0 * scale),
            )
            destination = (
                slice(top * scale, bottom * scale),
                slice(left * scale, right * scale),
            )
            output[destination] = numpy.clip(piece[area], 0, 255).astype(numpy.uint8)
            completed += 1
            report(progress, "processing", completed=completed, total=total,
                   unit="tiles", operation="superres")
    return output


def upscale(image: Image.Image, *, model: str = "realesrgan-x4plus",
            backend: str | None = None, device: str | None = None,
            tile: int = 256, overlap: int = 16, accept: bool = False,
            runner: Runner | None = None,
            progress: Any = None) -> tuple[Image.Image, dict[str, Any]]:
    from imagen_cli.application.progress import current_progress, report

    progress = progress if progress is not None else current_progress.get()
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
        report(progress, "loading_model", operation=model)
        runner, engine = _make_runner(model, backend=backend, device=device, accept=accept)
    alpha = image.getchannel("A") if "A" in image.getbands() else None
    array = numpy.asarray(image.convert("RGB"), dtype=numpy.uint8)
    result = tiled_upscale(array, runner, scale=scale, tile=tile, overlap=overlap,
                          progress=progress)
    converted = Image.fromarray(result, "RGB")
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
            "task": entry.get("task", "upscale"),
            "category": entry.get("category", "general"),
            "usage": entry.get("usage"),
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


def fetch_choices() -> list[tuple[str, str]]:
    """Weights catalog as picker rows, shared by the CLI prompt and the TUI form."""
    return [
        (f"{name}: {entry['scale']}x | {entry['license']} | {entry['source']}", name)
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
    download: Callable[[str, Path], None] | None = None,
) -> dict[str, Any]:
    """Preview or install the inference runtime and requested model weights.

    Without model names only the runtime is handled. Nothing is installed or
    downloaded unless ``accept`` is true. Runtime installation and weight
    download are separate steps so a missing backend never silently falls back
    and weights never arrive by accident.
    """
    names = [] if models is None else ([models] if isinstance(models, str) else list(models))
    if len(set(names)) != len(names):
        raise ValueError("install needs distinct model names")
    for name in names:
        if name not in MANIFEST:
            raise ValueError("unknown super-resolution model")
    runtime_ready = bool(available_providers())
    runtime_data: dict[str, Any] | None = None
    if accept and not runtime_ready:
        if runtime_install is None:
            from imagen_cli.application import runtime_service

            runtime_install = runtime_service.install
        _, runtime_data = runtime_install(backend=backend, accept=True)
        runtime_ready = bool(runtime_data.get("installed")) or bool(available_providers())
    rows = []
    for name in names:
        entry = MANIFEST[name]
        path = weights_path(name)
        row: dict[str, Any] = {
            "model": name,
            "task": entry.get("task", "upscale"),
            "category": entry.get("category", "general"),
            "scale": entry["scale"],
            "license": entry["license"],
            "source": entry["source"],
            "target": str(path),
            "downloadable": entry.get("url") is not None,
            "downloaded": path.exists(),
        }
        if accept:
            path = ensure_weights(name, accept=True, download=download)
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
            "Review each license and source, then rerun with --accept to install "
            "the runtime and download the selected weights."
            if names
            else "No model selected: --accept installs only the runtime. "
            "Add --model NAME (see: imagen superres models) to include weights."
        )
    return data


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
            "ssim_metric": "local_gaussian_pillow_l_v1",
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
