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


def _run(source: str, pipeline_path: str, output: str, response: str) -> None:
    try:
        limits = "unavailable"
        if sys.platform == "linux":
            import os
            import resource

            # Bound additional virtual allocation, not already mapped shared libraries.
            pages = int(Path("/proc/self/statm").read_text().split()[0])
            ceiling = pages * os.sysconf("SC_PAGE_SIZE") + MAX_WORKING_BYTES
            resource.setrlimit(resource.RLIMIT_AS, (ceiling, ceiling))
            limits = "linux_address_space_plus_2gib"
        from imagen_cli.application.postprocess_service import render

        pipeline = json.loads(Path(pipeline_path).read_text(encoding="utf-8"))
        data, metadata = render(source, pipeline)
        Path(output).write_bytes(data)
        metadata["execution"] = {"isolation": "spawn_process", "memory_limit": limits}
        payload = {"ok": True, "metadata": metadata}
    except Exception as error:
        payload = {"ok": False, "error": f"{type(error).__name__}: {error}"}
    Path(response).write_text(json.dumps(payload), encoding="utf-8")


def execute(source: Path, pipeline: Any, *, timeout: float = 120,
            cancelled: Event | None = None) -> tuple[bytes, dict[str, Any]]:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    if cancelled is not None and cancelled.is_set():
        raise ValueError("postprocess cancelled")
    with tempfile.TemporaryDirectory(prefix="imagen-postprocess-") as directory:
        root = Path(directory)
        output, response, specification = (
            root / "result.image", root / "response.json", root / "input.json",
        )
        specification.write_text(json.dumps(pipeline, allow_nan=False), encoding="utf-8")
        deadline = time.monotonic() + timeout
        process = subprocess.Popen(
            [sys.executable, "-m", "imagen_cli.application.postprocess_worker",
             str(source), str(specification), str(output), str(response)],
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
