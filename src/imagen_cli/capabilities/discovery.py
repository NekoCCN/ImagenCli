"""Evidence-aware discovery and explicit, unauthenticated source refresh."""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from imagen_cli.domain.enums import CapabilityStatus
from imagen_cli.domain.models import CapabilityRecord

__all__ = [
    "DiscoveryReport", "discover", "effective_status", "refresh", "source_urls",
]

_RANK = {"U": 0, "N": 1, "D": 2, "I": 3, "T": 4}


@dataclass(frozen=True)
class DiscoveryReport:
    items: tuple[dict[str, Any], ...]
    evidence: str = "offline_persisted"


def effective_status(record: CapabilityRecord) -> str:
    """Offline evidence can never stand in for live evidence."""
    raw = record.support_status
    status = raw.value if hasattr(raw, "value") else str(raw)
    live = bool(record.live_evidence)
    offline = bool(record.offline_evidence)
    if status == "T" and not live:
        return "I" if offline else "D"
    if status == "I" and not offline:
        return "D"
    return status


def _entry(record: CapabilityRecord) -> dict[str, Any]:
    status = effective_status(record)
    if record.live_evidence:
        evidence_kind = "live"
    elif record.offline_evidence:
        evidence_kind = "offline_only"
    elif record.support_status == CapabilityStatus.DECLARED:
        evidence_kind = "declared_unverified"
    else:
        evidence_kind = "none"
    return {
        "id": record.id,
        "provider": record.provider,
        "family": record.family,
        "model": record.model,
        "model_revision": record.model_revision,
        "operation": record.operation.value,
        "support_status": status,
        "declared_status": str(getattr(record.support_status, "value", record.support_status)),
        "evidence": {
            "kind": evidence_kind,
            "offline_count": len(record.offline_evidence),
            "live_count": len(record.live_evidence),
            "promoted": status != getattr(
                record.support_status, "value", record.support_status,
            ),
        },
        "transport_kind": record.transport_kind,
        "continuation_strategy": record.continuation_strategy,
        "idempotency_contract": dict(record.idempotency_contract),
        "cancel_contract": dict(record.cancel_contract),
        "sources": [source.model_dump(mode="json") for source in record.sources],
        "checked_at": record.checked_at,
    }


def discover(
    records: Iterable[CapabilityRecord],
    *,
    provider: str | None = None,
    family: str | None = None,
    model: str | None = None,
    operation: str | None = None,
) -> DiscoveryReport:
    selected = [
        _entry(record) for record in records
        if (provider is None or record.provider == provider)
        and (family is None or record.family == family)
        and (model is None or record.model == model)
        and (operation is None or record.operation.value == operation)
    ]
    selected.sort(key=lambda item: (
        item["provider"], item["family"], item["model"], item["operation"],
    ))
    return DiscoveryReport(tuple(selected))


def source_urls(records: Iterable[CapabilityRecord]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for record in records:
        for source in record.sources:
            if source.url not in seen:
                seen.add(source.url)
                result.append(source.url)
    return tuple(result)


def _public_url(value: str) -> bool:
    parsed = urlsplit(value)
    return (
        parsed.scheme in {"https", "http"}
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


def refresh(
    records: Iterable[CapabilityRecord],
    *,
    dry_run: bool,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 10.0,
    now: str | None = None,
) -> dict[str, Any]:
    """Re-read public source URLs; never authenticates and never promotes a status.

    Dry-run returns the exact URL plan and performs zero requests. A live call is
    only made with an explicitly supplied transport (tests use MockTransport).
    """
    materialized = tuple(records)
    urls = source_urls(materialized)
    invalid = [url for url in urls if not _public_url(url)]
    if invalid:
        raise ValueError("source URLs must be credential-free HTTP(S) URLs")
    checked_at = now or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    if dry_run:
        return {
            "mode": "dry_run", "authenticated": False, "requests": 0,
            "sources": list(urls), "status_changes": [], "checked_at": checked_at,
        }
    if transport is None:
        raise ValueError("explicit transport required for a source refresh")
    results: dict[str, int] = {}
    with httpx.Client(
        transport=transport, follow_redirects=False, trust_env=False,
        timeout=httpx.Timeout(timeout, connect=timeout),
    ) as http:
        for url in urls:
            # No credential resolver is accepted; this request can only be anonymous.
            response = http.get(url, headers={"Accept": "text/html"})
            try:
                results[url] = response.status_code
            finally:
                response.close()
    return {
        "mode": "source_check", "authenticated": False, "requests": len(results),
        "sources": list(urls), "results": results, "status_changes": [],
        "checked_at": checked_at,
        "evidence": [
            {"kind": "source_reachable", "url": url, "http_status": status}
            for url, status in results.items()
        ],
    }


def dumps(report: DiscoveryReport) -> str:
    return json.dumps(
        {"items": list(report.items), "evidence": report.evidence},
        ensure_ascii=False, sort_keys=True,
    )
