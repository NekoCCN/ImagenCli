"""Local termination by verified executor identity; cancellation is never faked."""

import os
import signal
from pathlib import Path
from typing import Any

from imagen_cli.storage.repositories import Repositories


def process_start(pid: int) -> str | None:
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    fields = text[text.rindex(")") + 2:].split()
    return fields[19] if len(fields) > 19 else None


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
