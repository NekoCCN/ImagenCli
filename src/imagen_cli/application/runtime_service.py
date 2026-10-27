"""Explicit installation of optional inference packages into this interpreter's venv."""

from __future__ import annotations

import shutil
import subprocess
import sys
from importlib import metadata
from typing import Any

BACKENDS = {
    "cpu": "onnxruntime",
    "cuda": "onnxruntime-gpu",
    "openvino": "onnxruntime-openvino",
}


def version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def install(*, backend: str = "cpu", accept: bool = False) -> tuple[str, dict[str, Any]]:
    if backend not in BACKENDS:
        raise ValueError("unknown runtime backend")
    if sys.prefix == sys.base_prefix:
        raise ValueError(
            "runtime installation requires a virtual environment; system Python is refused"
        )
    package = BACKENDS[backend]
    runtimes = (*BACKENDS.values(), "onnxruntime-directml")
    installed = {name: version(name) for name in (*runtimes, "onnx", "numpy")}
    conflicts = [name for name in runtimes if name != package and installed[name]]
    if conflicts:
        raise ValueError("ONNX runtime backend conflict; remove the other backend explicitly first")
    uv = shutil.which("uv")
    requirements = [package + ">=1.20", "onnx>=1.20", "numpy>=2"]
    if uv:
        command = [uv, "pip", "install", "--python", sys.executable, *requirements]
    else:
        command = [sys.executable, "-m", "pip", "install", *requirements]
    data: dict[str, Any] = {
        "backend": backend,
        "environment": sys.prefix,
        "python": sys.executable,
        "command": command,
        "packages": installed,
        "installed": False,
    }
    if not accept:
        return "succeeded", data
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ValueError(
            "runtime installation timed out; inspect the environment before retrying"
        ) from None
    except OSError:
        raise ValueError("runtime installer could not be started") from None
    if result.returncode:
        # Installer output can include authenticated index URLs; never relay it.
        raise ValueError(f"runtime installation failed (exit {result.returncode})")
    metadata.MetadataPathFinder.invalidate_caches()
    data.update(installed=True, packages={name: version(name) for name in installed})
    return "succeeded", data
