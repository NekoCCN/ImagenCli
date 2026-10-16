"""Capability records: canonical digest and unique evidence-backed declarations."""

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from imagen_cli.domain.models import CapabilityRecord

__all__ = ["build", "compute_digest", "unique"]


def _canonical(data: dict[str, Any]) -> str:
    return json.dumps(
        {key: value for key, value in data.items() if key != "digest"},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    )


def compute_digest(data: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(data).encode("utf-8")).hexdigest()


def build(data: dict[str, Any]) -> CapabilityRecord:
    return CapabilityRecord.model_validate({**data, "digest": compute_digest(data)})


def unique(records: Iterable[CapabilityRecord]) -> tuple[CapabilityRecord, ...]:
    seen: set[tuple[str, str, str, str, str]] = set()
    result: list[CapabilityRecord] = []
    for record in records:
        key = (
            record.provider, record.family, record.model,
            record.model_revision, str(record.operation),
        )
        if key in seen:
            raise ValueError("duplicate capability declaration")
        seen.add(key)
        result.append(record)
    return tuple(result)
