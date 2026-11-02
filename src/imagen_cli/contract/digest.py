"""The one job digest used for approval and idempotency."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from .canonical import canonical

DIGEST_FIELDS = (
    "schema_version",
    "provider",
    "endpoint_host",
    "gpu",
    "operation",
    "pipeline",
    "normalized_params",
    "input_sha256",
    "upload_bytes",
    "max_seconds",
    "worker_image_digest",
    "license",
)


def job_digest(record: Mapping[str, Any]) -> str:
    missing = sorted(set(DIGEST_FIELDS) - set(record))
    unknown = sorted(set(record) - set(DIGEST_FIELDS))
    if missing or unknown:
        raise ValueError(
            f"job digest fields mismatch; missing: {missing or 'none'}; "
            f"unknown: {unknown or 'none'}"
        )
    return hashlib.sha256(canonical(record)).hexdigest()
