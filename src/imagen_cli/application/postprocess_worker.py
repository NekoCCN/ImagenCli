"""Short-lived image workers: parent alone publishes artifacts after success."""

from __future__ import annotations

import json
import math
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from threading import Event
from typing import Any

MAX_WORKING_BYTES = 2 * 1024**3
# ONNX inference reserves address space for weights, arenas and float32 feature maps
# far beyond the pixel buffers the pixel budget counts.
MODEL_RUNTIME_BYTES = 6 * 1024**3
TRANSFORM_TIMEOUT = 120.0
# A 4x model needs minutes on CPU well before images reach the pixel budget.
MODEL_TIMEOUT = 900.0


def _has_model_steps(pipeline: Any) -> bool:
    steps = pipeline if isinstance(pipeline, list) else []
    return any(isinstance(step, dict) and step.get("op") == "superres" for step in steps)


def default_timeout(pipeline: Any) -> float:
    return MODEL_TIMEOUT if _has_model_steps(pipeline) else TRANSFORM_TIMEOUT


def _allocation_failure(error: BaseException) -> bool:
    """Memory exhaustion from a native runtime, worded for a terminal."""
    text = str(error).lower()
    return any(phrase in text for phrase in (
        "failed to allocate memory", "bad_alloc", "out of memory",
        "cannot allocate memory", "memoryerror",
    ))


def _error_message(error: BaseException) -> str:
    if _allocation_failure(error):
        return (
            "not enough memory for this operation; retry with a smaller tile "
            "or a smaller image"
        )
    if isinstance(error, ValueError):
        # User-facing text: the class name only helps when the failure is unexpected.
        return str(error)
    return f"{type(error).__name__}: {error}"


def _address_space_extra(pipeline: Any) -> int:
    return MODEL_RUNTIME_BYTES if _has_model_steps(pipeline) else MAX_WORKING_BYTES


def _run(source: str, pipeline_path: str, output: str, response: str,
         approval: str = "") -> None:
    try:
        pipeline = json.loads(Path(pipeline_path).read_text(encoding="utf-8"))
        limits = "unavailable"
        if sys.platform == "linux":
            import os
            import resource

            # Bound additional virtual allocation, not already mapped shared libraries.
            pages = int(Path("/proc/self/statm").read_text().split()[0])
            extra = _address_space_extra(pipeline)
            ceiling = pages * os.sysconf("SC_PAGE_SIZE") + extra
            resource.setrlimit(resource.RLIMIT_AS, (ceiling, ceiling))
            limits = f"linux_address_space_plus_{extra // 1024**2}mib"
        from imagen_cli.application.postprocess_service import budget_preflight, render

        consent = budget_preflight(Path(source), pipeline, approval or None)
        data, metadata = render(source, pipeline, allow_large=bool(consent))
        metadata.update(consent)
        Path(output).write_bytes(data)
        metadata["execution"] = {"isolation": "spawn_process", "memory_limit": limits}
        payload = {"ok": True, "metadata": metadata}
    except Exception as error:
        payload = {"ok": False, "error": _error_message(error)}
    Path(response).write_text(json.dumps(payload), encoding="utf-8")


def execute(source: Path, pipeline: Any, *, timeout: float | None = None,
            cancelled: Event | None = None,
            budget_approval: str | None = None) -> tuple[bytes, dict[str, Any]]:
    if timeout is None:
        timeout = default_timeout(pipeline)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    if cancelled is not None and cancelled.is_set():
        raise ValueError("postprocess cancelled")
    from imagen_cli.application.postprocess_service import budget_preflight

    consent = budget_preflight(source, pipeline, budget_approval)
    with tempfile.TemporaryDirectory(prefix="imagen-postprocess-") as directory:
        root = Path(directory)
        output, response, specification = (
            root / "result.image", root / "response.json", root / "input.json",
        )
        specification.write_text(json.dumps(pipeline, allow_nan=False), encoding="utf-8")
        deadline = time.monotonic() + timeout
        process = subprocess.Popen(
            [sys.executable, "-m", "imagen_cli.application.postprocess_worker",
             str(source), str(specification), str(output), str(response),
             budget_approval or ""],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            while process.poll() is None:
                if cancelled is not None and cancelled.is_set():
                    raise ValueError("postprocess cancelled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("postprocess timeout")
                try:
                    process.wait(timeout=min(0.02, remaining))
                except subprocess.TimeoutExpired:
                    pass
            if cancelled is not None and cancelled.is_set():
                raise ValueError("postprocess cancelled")
            if not response.is_file():
                raise ValueError(f"postprocess worker exited with code {process.returncode}")
            payload = json.loads(response.read_text(encoding="utf-8"))
            if not payload["ok"]:
                raise ValueError(payload["error"])
            metadata: dict[str, Any] = payload["metadata"]
            metadata.update(consent)
            metadata["execution"]["timeout_seconds"] = timeout
            return output.read_bytes(), metadata
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    _run(*sys.argv[1:])
