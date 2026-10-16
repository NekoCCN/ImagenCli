"""Batch commands return structured data; no prompt, preview or child-state rewriting."""

from collections.abc import Mapping, Sequence
from typing import Any

from imagen_cli.application.batch_service import BatchService
from imagen_cli.domain.contracts import CreationInput


def submit(
    service: BatchService,
    requests: Sequence[CreationInput | Mapping[str, Any]],
    concurrency: int | None = None,
) -> tuple[str, dict[str, Any]]:
    return service.submit(requests, concurrency)


def get_(service: BatchService, batch_id: str) -> tuple[str, dict[str, Any]]:
    return service.get(batch_id)


def wait_(
    service: BatchService, batch_id: str, *, timeout: int = 300
) -> tuple[str, dict[str, Any]]:
    return service.wait(batch_id, timeout=timeout)
