"""Server-derived job paths; a client never supplies a filesystem location."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

JOB_ID = re.compile(r"^job_[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}$")
CLIENT_PATH_FIELDS = ("input", "output", "input_path", "output_path")


def validate_job_id(job_id: str) -> str:
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        raise ValueError("invalid job id")
    return job_id


def job_paths(root: Path, job_id: str) -> dict[str, Path]:
    validate_job_id(job_id)
    directory = Path(root) / job_id
    return {
        "directory": directory,
        "input": directory / "input.bin",
        "output": directory / "output.bin",
    }


def reject_client_paths(payload: Mapping[str, Any]) -> None:
    present = sorted(set(payload) & set(CLIENT_PATH_FIELDS))
    if present:
        raise ValueError("client-supplied paths are not accepted: " + ", ".join(present))
