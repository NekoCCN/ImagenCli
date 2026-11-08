"""Derive a cached fp16 copy of a pinned matting weight.

A 4 GB card cannot run ``birefnet-lite`` at its 1024x1024 input in fp32: one
allocation of 822 MB fails, and tiling does not help because every tile is
resized to the fixed 1024x1024 graph input. Half precision fits (109 MB of
weights, measured ~6.5 s per forward) and the alpha difference stays inside the
tolerance pinned by ``tests/unit/test_matting_precision.py``.

The source weight must match its pinned SHA-256 before anything is derived, and
the derived file is only published through an atomic rename.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from importlib import metadata
from pathlib import Path
from typing import Any


class UnsupportedInThisImage(RuntimeError):
    """This image has no fp16 toolchain; the fp32 path still works."""


def _weights_path(model: str) -> Path:
    from imagen_cli.application.matting import weights_path

    return weights_path(model)


def _manifest_digest(model: str) -> str | None:
    from imagen_cli.application.matting import MANIFEST

    entry = MANIFEST.get(model) or {}
    digest = entry.get("sha256")
    return str(digest) if digest else None


def derived_path(model: str) -> Path:
    return _weights_path(model).with_suffix(".fp16.onnx")


def fingerprint_path(model: str) -> Path:
    return _weights_path(model).with_suffix(".fp16.json")


def _version(name: str) -> str:
    for candidate in (name, name.replace("onnxruntime", "onnxruntime-gpu")):
        try:
            return metadata.version(candidate)
        except metadata.PackageNotFoundError:
            continue
    return "absent"


def _fingerprint() -> dict[str, Any]:
    return {
        "onnxconverter_common": _version("onnxconverter-common"),
        "onnx": _version("onnx"),
        "onnxruntime": _version("onnxruntime"),
        "keep_io_types": True,
    }


def _default_convert(source: Path, target: Path) -> None:
    try:
        # Optional ai-onnx extra: absent images run fp32 instead.
        import onnx  # pyright: ignore[reportMissingImports]
        from onnxconverter_common import float16  # type: ignore[import-not-found]
    except ImportError as error:
        raise UnsupportedInThisImage(
            "fp16 derivation needs onnxconverter-common and onnx; this image runs "
            "fp32 only. Install the ai-onnx extra or pass --precision fp32."
        ) from error
    model = onnx.load(str(source))
    converted = float16.convert_float_to_float16(model, keep_io_types=True)
    onnx.save(converted, str(target))


def ensure_fp16(
    model: str, *, convert: Callable[[Path, Path], None] | None = None
) -> tuple[Path, dict[str, Any]]:
    """Return the cached fp16 weights, deriving them when the cache is stale."""
    source = _weights_path(model)
    if not source.is_file():
        raise ValueError(f"matting weights for {model} are not installed")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    expected = _manifest_digest(model)
    if expected is not None and digest != expected:
        raise ValueError("matting weights digest mismatch; reinstall the model")

    derived = derived_path(model)
    fingerprint_file = fingerprint_path(model)
    wanted = {"source_sha256": digest, **_fingerprint()}
    if derived.is_file() and fingerprint_file.is_file():
        try:
            cached = json.loads(fingerprint_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached = None
        if cached == wanted:
            return derived, {"derived": False, "fingerprint": wanted}

    convert = convert or _default_convert
    partial = derived.with_suffix(".fp16.part")
    try:
        convert(source, partial)
        os.replace(partial, derived)
    except ImportError as error:
        partial.unlink(missing_ok=True)
        raise UnsupportedInThisImage(
            "fp16 derivation needs onnxconverter-common and onnx; this image runs "
            "fp32 only. Install the ai-onnx extra or pass --precision fp32."
        ) from error
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    fingerprint_file.write_text(json.dumps(wanted, sort_keys=True, indent=2),
                                encoding="utf-8")
    return derived, {"derived": True, "fingerprint": wanted}
