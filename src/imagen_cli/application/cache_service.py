"""Cache audit and confirmed reclamation; reports file bytes only."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from imagen_cli.storage import queries
from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.repositories import Repositories
from imagen_cli.storage.safety import safe_path

TERMINAL_JOB_STATES = {"succeeded", "failed", "canceled"}
STAGING_STATES = {"staging", "save_failed"}
ROW_ONLY_KINDS = {"expired_approval", "expired_plan"}


@dataclass(frozen=True)
class Entry:
    kind: str
    identity: str
    bytes: int
    detail: dict[str, Any]

    def as_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "id": self.identity, "bytes": self.bytes, **self.detail}


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return _utc(value)
    except ValueError:
        return None


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _paths(row: dict[str, Any]) -> list[str]:
    names = {row.get("staging_relpath"), row.get("managed_relpath")}
    return sorted(name for name in names if isinstance(name, str))


def audit(
    repositories: Repositories,
    store: ArtifactStore,
    *,
    now: datetime | None = None,
    staging_days: int = 7,
    deleted_days: int = 30,
) -> list[Entry]:
    """Classify reclaimable rows; kinds are mutually exclusive by save_state."""
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    if staging_days < 0 or deleted_days < 0:
        raise ValueError("retention days must be non-negative")
    sessions = {row["id"]: row for row in queries.iter_all(repositories, "sessions")}
    holders = queries.reference_sessions(repositories)
    jobs = {row["id"]: row for row in queries.iter_all(repositories, "jobs")}
    entries: list[Entry] = []

    for row in queries.iter_all(repositories, "artifacts", include_private=True):
        identity = row["id"]
        state = row["save_state"]
        if state in STAGING_STATES:
            created = _parse(row.get("created_at"))
            producer = row.get("producer_job_id")
            job = jobs.get(producer) if isinstance(producer, str) else None
            if created is None or moment - created < timedelta(days=staging_days):
                continue
            if job is not None and job.get("state") not in TERMINAL_JOB_STATES:
                continue
            size = sum(_size(safe_path(store.root, name)) for name in _paths(row))
            entries.append(Entry("staging_artifact", identity, size, {"save_state": state}))
            continue
        if state != "published":
            continue
        holding = holders.get(identity, set())
        if not holding:
            size = sum(_size(safe_path(store.root, name)) for name in _paths(row))
            entries.append(Entry("unreferenced_artifact", identity, size, {}))
            continue
        deleted = [sessions[s]["deleted_at"] for s in holding if s in sessions]
        if len(deleted) != len(holding) or not all(deleted):
            continue
        stamps = [_parse(value) for value in deleted]
        if any(stamp is None for stamp in stamps):
            continue
        oldest = max(stamp for stamp in stamps if stamp is not None)
        if moment - oldest < timedelta(days=deleted_days):
            continue
        size = sum(_size(safe_path(store.root, name)) for name in _paths(row))
        entries.append(Entry("deleted_session_artifact", identity, size, {}))

    approvals = {row["plan_id"]: row for row in queries.iter_all(repositories, "approvals")}
    jobs_by_plan = {
        row["plan_id"] for row in jobs.values() if isinstance(row.get("plan_id"), str)
    }
    for row in queries.iter_all(repositories, "approvals"):
        expires = _parse(row.get("expires_at"))
        if expires is not None and expires < moment and row.get("state") != "consumed":
            entries.append(Entry("expired_approval", row["id"], 0, {"state": row["state"]}))
    for row in queries.iter_all(repositories, "plans"):
        expires = _parse(row.get("expires_at"))
        if expires is None or expires >= moment:
            continue
        if row["id"] in jobs_by_plan or row["id"] in approvals:
            continue
        entries.append(Entry("expired_plan", row["id"], 0, {}))

    return sorted(entries, key=lambda item: (item.kind, item.identity))


def digest(entries: list[Entry]) -> str:
    payload = [[item.kind, item.identity, item.bytes] for item in entries]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def report(entries: list[Entry]) -> dict[str, Any]:
    total = sum(item.bytes for item in entries)
    return {
        "entries": [item.as_json() for item in entries],
        "plan_digest": digest(entries),
        "planned_bytes": total,
        "count": len(entries),
    }


def purge(
    repositories: Repositories,
    store: ArtifactStore,
    plan_digest: str,
    *,
    clock: Callable[[], datetime] | None = None,
    staging_days: int = 7,
    deleted_days: int = 30,
) -> dict[str, Any]:
    """Delete exactly the confirmed plan; any audit drift refuses the whole run."""
    now = (clock or (lambda: datetime.now(UTC)))().astimezone(UTC)
    with repositories.transaction():
        entries = audit(
            repositories, store, now=now,
            staging_days=staging_days, deleted_days=deleted_days,
        )
        if digest(entries) != plan_digest:
            raise ValueError("cache plan digest mismatch")
        results: list[dict[str, Any]] = []
        planned = sum(item.bytes for item in entries)
        freed = 0
        for entry in sorted(entries, key=lambda item: (item.kind, item.identity)):
            if entry.kind in ROW_ONLY_KINDS:
                table = "approvals" if entry.kind == "expired_approval" else "plans"
                repositories.delete(table, entry.identity)
                results.append({"id": entry.identity, "kind": entry.kind, "result": "deleted"})
                continue
            row = repositories.get("artifacts", entry.identity, include_private=True)
            if row is None:
                results.append({
                    "id": entry.identity, "kind": entry.kind, "result": "already_missing",
                })
                continue
            failed: OSError | None = None
            released = 0
            for name in _paths(row):
                path = safe_path(store.root, name)
                try:
                    size = path.stat().st_size
                    path.unlink()
                    released += size
                except FileNotFoundError:
                    continue
                except OSError as error:
                    failed = error
                    break
            if failed is not None:
                results.append({
                    "id": entry.identity, "kind": entry.kind,
                    "result": "failed", "error": str(failed),
                })
                continue
            queries.delete_references(repositories, entry.identity)
            repositories.delete("artifacts", entry.identity)
            freed += released
            results.append({"id": entry.identity, "kind": entry.kind, "result": "deleted"})
        return {
            "planned_bytes": planned,
            "deleted_bytes": freed,
            "results": results,
            "remaining": sum(1 for item in results if item["result"] == "failed"),
        }
