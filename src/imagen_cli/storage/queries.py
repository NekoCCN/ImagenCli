"""Shared storage queries; the single source for ownership and pagination."""

from __future__ import annotations

from typing import Any

from imagen_cli.storage.repositories import Repositories

PAGE = 1000


def iter_all(
    repositories: Repositories,
    table: str,
    *,
    include_private: bool = False,
    **filters: Any,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    while True:
        page = repositories.list(
            table, limit=PAGE, offset=len(rows), include_private=include_private, **filters
        )
        rows.extend(page)
        if len(page) < PAGE:
            return rows


def session_outputs(repositories: Repositories, session_id: str) -> list[dict[str, Any]]:
    """Published outputs across turn references and legacy local-result links."""
    turns = sorted(iter_all(repositories, "turns", session_id=session_id),
                   key=lambda row: (row.get("created_at", ""), row["id"]))
    references = iter_all(repositories, "artifact_references", session_id=session_id)
    candidates = [
        (reference, turn)
        for turn in turns
        for reference in sorted(
            (ref for ref in references if ref["turn_id"] == turn["id"]),
            key=lambda ref: int(ref.get("index") or 0),
        )
    ]
    candidates.extend((link, {}) for link in iter_all(
        repositories, "artifact_links", session_id=session_id,
    ))
    seen: set[str] = set()
    outputs = []
    for reference, turn in candidates:
        identity = reference["artifact_id"]
        if reference.get("role") != "output" or identity in seen:
            continue
        artifact = repositories.get("artifacts", identity)
        if artifact is None or artifact.get("save_state") != "published":
            continue
        seen.add(identity)
        manifest = artifact.get("origin_manifest") or {}
        operation = turn.get("operation") or (
            "superres" if any(step.get("op") == "superres"
                              for step in manifest.get("pipeline", []))
            else "transform" if manifest.get("kind") == "postprocess" else ""
        )
        outputs.append({**artifact, "turn_id": turn.get("id"),
                        "operation": operation, "state": turn.get("creation_state", "")})
    return outputs


def live_sessions(repositories: Repositories) -> set[str]:
    return {
        row["id"] for row in iter_all(repositories, "sessions")
        if row.get("deleted_at") is None
    }


def reference_sessions(repositories: Repositories) -> dict[str, set[str]]:
    """Artifact identity -> sessions holding it, across both ownership tables."""
    sessions: dict[str, set[str]] = {}
    for row in iter_all(repositories, "artifact_references"):
        sessions.setdefault(row["artifact_id"], set()).add(row["session_id"])
    for row in iter_all(repositories, "artifact_links"):
        sessions.setdefault(row["artifact_id"], set()).add(row["session_id"])
    return sessions


def live_references(repositories: Repositories) -> set[str]:
    live = live_sessions(repositories)
    return {
        identity for identity, holders in reference_sessions(repositories).items()
        if holders & live
    }


def invalidate_models(repositories: Repositories, profile_id: str) -> int:
    """Endpoint changes invalidate every declared model for that profile."""
    cursor = repositories.connection.execute(
        'DELETE FROM "provider_models" WHERE profile_id=?', (profile_id,)
    )
    return cursor.rowcount


def delete_references(repositories: Repositories, artifact_id: str) -> int:
    """Remove every ownership row for one artifact; both tables are required."""
    removed = 0
    for table in ("artifact_references", "artifact_links"):
        cursor = repositories.connection.execute(
            f'DELETE FROM "{table}" WHERE artifact_id=?', (artifact_id,)
        )
        removed += cursor.rowcount
    return removed
