"""Short-lived image workers: parent alone publishes artifacts after success."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Protocol

from imagen_cli import settings
from imagen_cli.application.progress import ProgressCallback, report


class Cancellation(Protocol):
    def is_set(self) -> bool: ...

MAX_WORKING_BYTES = settings.DEFAULT_MEMORY_BYTES
# ONNX inference reserves address space for weights, arenas and float32 feature maps
# far beyond the pixel buffers the pixel budget counts.
MODEL_RUNTIME_BYTES = 6 * 1024**3
TRANSFORM_TIMEOUT = 120.0
# A 4x model needs minutes on CPU well before images reach the pixel budget.
MODEL_TIMEOUT = 900.0
# Seconds per megapixel measured with the shipped CPU solvers. The fixed 120 s
# deadline killed 4K jobs that were still making progress.
SECONDS_PER_MEGAPIXEL = {
    "rtv_smooth": 40.0, "nl_means": 35.0, "wls_smooth": 25.0,
    "weighted_median": 5.0, "l0_smooth": 4.0,
    "bilateral": 3.0, "guided": 3.0, "kuwahara": 2.0,
    "domain_transform": 1.0,
}
DEFAULT_SECONDS_PER_MEGAPIXEL = 4.0


def _has_model_steps(pipeline: Any) -> bool:
    steps = pipeline if isinstance(pipeline, list) else []
    return any(isinstance(step, dict) and step.get("op") == "superres" for step in steps)


def default_timeout(pipeline: Any, pixels: int = 0) -> float:
    configured = settings.timeout_limit()
    if configured is not None:
        return configured
    if _has_model_steps(pipeline):
        return MODEL_TIMEOUT
    steps = pipeline if isinstance(pipeline, list) else []
    cost = sum(
        SECONDS_PER_MEGAPIXEL.get(step.get("op", ""), DEFAULT_SECONDS_PER_MEGAPIXEL)
        for step in steps if isinstance(step, dict)
    )
    return TRANSFORM_TIMEOUT + cost * max(0, pixels) / 1_000_000


def source_pixels(source: Path) -> int:
    """Header-only pixel count, used to size the deadline."""
    from PIL import Image

    from imagen_cli.storage.artifacts import image_limits

    with image_limits(True), Image.open(source) as opened:
        return opened.width * opened.height


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


def _address_space_extra(pipeline: Any, consent: dict[str, Any] | None = None) -> int | None:
    """Extra address space the worker may map; None means the cap was disabled."""
    configured = settings.memory_limit()
    if configured == 0:
        return None
    base = MAX_WORKING_BYTES if configured is None else configured
    if _has_model_steps(pipeline):
        return max(MODEL_RUNTIME_BYTES, base)
    # Consent must buy the run: the fixed ceiling would kill an approved job.
    estimated = int((consent or {}).get("budget", {}).get("estimated_working_bytes", 0))
    return max(base, int(estimated * 1.5))


def _run(source: str, pipeline_path: str, output: str, response: str,
         approval: str = "", progress_path: str = "") -> None:
    def update(value: dict[str, Any]) -> None:
        if progress_path:
            with Path(progress_path).open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(value) + "\n")

    try:
        pipeline = json.loads(Path(pipeline_path).read_text(encoding="utf-8"))
        from imagen_cli.application.postprocess_service import budget_preflight, render

        consent = budget_preflight(Path(source), pipeline, approval or None)
        limits = "unavailable"
        if sys.platform == "linux":
            import os
            import resource

            # Bound additional virtual allocation, not already mapped shared libraries.
            pages = int(Path("/proc/self/statm").read_text().split()[0])
            extra = _address_space_extra(pipeline, consent)
            if extra is None:
                limits = "unlimited"
            else:
                ceiling = pages * os.sysconf("SC_PAGE_SIZE") + extra
                resource.setrlimit(resource.RLIMIT_AS, (ceiling, ceiling))
                limits = f"linux_address_space_plus_{extra // 1024**2}mib"
        data, metadata = render(source, pipeline, allow_large=bool(consent), progress=update)
        metadata.update(consent)
        Path(output).write_bytes(data)
        metadata["execution"] = {"isolation": "spawn_process", "memory_limit": limits}
        payload = {"ok": True, "metadata": metadata}
    except Exception as error:
        payload = {"ok": False, "error": _error_message(error)}
    Path(response).write_text(json.dumps(payload), encoding="utf-8")


def execute(source: Path, pipeline: Any, *, timeout: float | None = None,
            cancelled: Cancellation | None = None,
            budget_approval: str | None = None,
            progress: ProgressCallback | None = None,
            source_sha256: str | None = None) -> tuple[bytes, dict[str, Any]]:
    if timeout is not None and (not math.isfinite(timeout) or timeout < 0):
        raise ValueError("timeout must be a non-negative number of seconds")
    if cancelled is not None and cancelled.is_set():
        raise ValueError("postprocess cancelled")
    from imagen_cli.application.postprocess_service import budget_preflight

    source_bytes = source.read_bytes()
    if source_sha256 is not None and hashlib.sha256(source_bytes).hexdigest() != source_sha256:
        raise ValueError("source artifact bytes changed")
    report(progress, "preparing")
    with tempfile.TemporaryDirectory(prefix="imagen-postprocess-") as directory:
        root = Path(directory)
        snapshot = root / "source.image"
        snapshot.write_bytes(source_bytes)
        if timeout is None:
            timeout = default_timeout(pipeline, source_pixels(snapshot))
        consent = budget_preflight(snapshot, pipeline, budget_approval)
        output, response, specification = (
            root / "result.image", root / "response.json", root / "input.json",
        )
        progress_file = root / "progress.jsonl"
        progress_file.touch()
        position = 0

        def drain_progress() -> None:
            nonlocal position
            with progress_file.open(encoding="utf-8") as stream:
                stream.seek(position)
                while line := stream.readline():
                    if not line.endswith("\n"):
                        break
                    position = stream.tell()
                    if progress is not None:
                        progress(json.loads(line))

        specification.write_text(json.dumps(pipeline, allow_nan=False), encoding="utf-8")
        deadline = math.inf if timeout == 0 else time.monotonic() + timeout
        process = subprocess.Popen(
            [sys.executable, "-m", "imagen_cli.application.postprocess_worker",
             str(snapshot), str(specification), str(output), str(response),
             budget_approval or "", str(progress_file)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            while process.poll() is None:
                drain_progress()
                if cancelled is not None and cancelled.is_set():
                    raise ValueError("postprocess cancelled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("postprocess timeout")
                try:
                    process.wait(timeout=min(0.02, remaining))
                except subprocess.TimeoutExpired:
                    pass
            drain_progress()
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
