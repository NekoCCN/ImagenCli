"""Declare intent-only capability records for a profile's own model.

The built-in catalog is reviewed offline data; a custom endpoint's models need
an explicit declaration before planning can run. The declaration borrows the
schema and transport from the reviewed template for the same provider/family.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any, cast

from imagen_cli.capabilities.catalog_data import CANONICAL_RECORDS
from imagen_cli.capabilities.records import build
from imagen_cli.storage import queries
from imagen_cli.storage.repositories import Repositories

OPERATIONS = ("generate", "edit")


def _profile(repositories: Repositories, identity: str) -> dict[str, Any]:
    row = repositories.get("profiles", identity)
    if row is not None:
        return cast(dict[str, Any], row)
    matches = [
        item for item in queries.iter_all(repositories, "profiles")
        if item.get("name") == identity
    ]
    if len(matches) != 1:
        raise KeyError(identity)
    return matches[0]


def declare(
    repositories: Repositories, identity: str, operation: str = "generate", *,
    model: str | None = None, revision: str = "declared", now: datetime | None = None,
) -> tuple[str, dict[str, Any]]:
    if operation not in OPERATIONS:
        raise ValueError("operation must be generate or edit")
    profile = _profile(repositories, identity)
    chosen = model or str(profile.get("model_default") or "")
    if not chosen:
        raise ValueError("profile has no default model; set one or pass --model")
    for row in repositories.list("capabilities", limit=1000):
        if (
            row["provider"] == profile["provider"] and row["family"] == profile["family"]
            and row["model"] == chosen and row["model_revision"] == revision
            and row["operation"] == operation
        ):
            return "succeeded", {"capability": row, "created": False}
    template = next((
        record for record in CANONICAL_RECORDS
        if record.provider == profile["provider"] and record.family == profile["family"]
        and str(record.operation) == operation
    ), None)
    if template is None:
        raise ValueError("no reviewed template for this provider/family/operation")
    stamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat().replace("+00:00", "Z")
    identity_digest = hashlib.sha256(
        f"{profile['id']}|{chosen}|{operation}|{revision}".encode()
    ).hexdigest()[:24]
    record = build({
        "id": f"declared-{identity_digest}",
        "provider": profile["provider"],
        "family": profile["family"],
        "model": chosen,
        "model_revision": revision,
        "operation": operation,
        "support_status": "I",
        "parameter_schema": template.parameter_schema,
        "semantic_mapping": template.semantic_mapping,
        "input_limits": template.input_limits,
        "transport_kind": template.transport_kind,
        "continuation_strategy": (
            template.continuation_strategy if operation == "generate"
            else "explicit_images"
        ),
        "idempotency_contract": template.idempotency_contract,
        "cancel_contract": template.cancel_contract,
        "sources": [{
            "url": str(profile["base_url"]),
            "date": stamp[:10],
            "locator": "capabilities declare",
        }],
        "checked_at": stamp,
        "offline_evidence": [],
        "live_evidence": [],
    })
    repositories.insert("capabilities", record.model_dump(mode="json"))
    return "succeeded", {"capability": record.model_dump(mode="json"), "created": True}
