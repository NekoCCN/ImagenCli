"""Local termination by verified executor identity; cancellation is never faked."""

import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

from imagen_cli.storage.repositories import Repositories


def _linux_start(pid: int) -> str | None:
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    fields = text[text.rindex(")") + 2:].split()
    return fields[19] if len(fields) > 19 else None


def _darwin_start(pid: int) -> str | None:
    try:
        done = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = done.stdout.strip()
    return value or None if done.returncode == 0 else None


def _windows_start(pid: int) -> str | None:
    import ctypes
    from ctypes import wintypes

    # A terminated process keeps its creation time while any handle holds the
    # process object, so the exit state must be checked too.
    process_query_limited_information = 0x1000
    synchronize = 0x00100000
    wait_timeout = 0x102
    if not hasattr(ctypes, "windll"):
        return None
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined, unused-ignore]
    handle = kernel32.OpenProcess(
        process_query_limited_information | synchronize, False, pid
    )
    if not handle:
        return None
    creation, exit_time = wintypes.FILETIME(), wintypes.FILETIME()
    kernel, user = wintypes.FILETIME(), wintypes.FILETIME()
    try:
        if kernel32.WaitForSingleObject(handle, 0) != wait_timeout:
            return None
        ok = kernel32.GetProcessTimes(
            handle, ctypes.byref(creation), ctypes.byref(exit_time),
            ctypes.byref(kernel), ctypes.byref(user),
        )
        if not ok:
            return None
        return f"{creation.dwHighDateTime:08x}{creation.dwLowDateTime:08x}"
    finally:
        kernel32.CloseHandle(handle)


def process_start(pid: int) -> str | None:
    """Stable per-process identity on Linux, macOS and Windows."""
    if type(pid) is not int or pid <= 0:
        return None
    if sys.platform.startswith("linux"):
        return _linux_start(pid)
    if sys.platform == "darwin":
        return _darwin_start(pid)
    if sys.platform.startswith("win"):
        return _windows_start(pid)
    return None


def executor_id() -> dict[str, Any] | None:
    start = process_start(os.getpid())
    return None if start is None else {"pid": os.getpid(), "start": start}


def cancel_local(repositories: Repositories, job_id: str) -> str:
    job = repositories.get("jobs", job_id)
    recorded = job.get("executor_id") if job else None
    if not job or job["state"] != "running" or not isinstance(recorded, dict):
        return "unsupported_capability"
    pid = recorded.get("pid")
    if type(pid) is not int or pid <= 1 or process_start(pid) != recorded.get("start"):
        return "unsupported_capability"
    repositories.append_event(job_id, "cancel_intent", {"scope": "local"})
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        repositories.append_event(job_id, "cancel_local_failed", {})
        return "conflict"
    repositories.append_event(job_id, "cancel_local", {"pid": pid})
    return "succeeded"
