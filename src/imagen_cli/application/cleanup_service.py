"""Logical session deletion and confirmed asset garbage collection."""

from datetime import UTC, datetime
from typing import Any, cast

from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.repositories import Repositories
from imagen_cli.storage.safety import safe_path


def delete_session(repositories: Repositories, session_id: str) -> dict[str, Any]:
    with repositories.transaction():
        session = repositories.get("sessions", session_id)
        if session is None:
            raise KeyError(session_id)
        if session.get("deleted_at") is None:
            session = repositories.update("sessions", session_id, {
                "deleted_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            })
        return cast(dict[str, Any], session)


def _all(repositories: Repositories, table: str) -> list[dict[str, Any]]:
    from imagen_cli.storage.queries import iter_all

    # staging_relpath is a private field; without it GC never unlinks staging files.
    return iter_all(repositories, table, include_private=table == "artifacts")


def _referenced(repositories: Repositories) -> set[str]:
    live = {
        row["id"] for row in _all(repositories, "sessions")
        if row.get("deleted_at") is None
    }
    return {
        ref["artifact_id"] for ref in _all(repositories, "artifact_references")
        if ref["session_id"] in live
    }


def collect(
    repositories: Repositories, store: ArtifactStore, *, confirmed: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Dry-run unless `confirmed` lists exactly the identities the caller accepted."""
    referenced = _referenced(repositories)
    candidates = [
        row for row in _all(repositories, "artifacts")
        if row["id"] not in referenced
    ]
    preview = [
        {"id": row["id"], "path": str(safe_path(store.root, row["managed_relpath"]))}
        for row in candidates
    ]
    if confirmed is None:
        return {"dry_run": True, "candidates": preview, "deleted": []}
    accepted = [row for row in candidates if row["id"] in set(confirmed)]
    deleted: list[str] = []
    for row in accepted:
        if row["id"] in _referenced(repositories):
            continue
        path = safe_path(store.root, row["managed_relpath"])
        path.unlink(missing_ok=True)
        staging = row.get("staging_relpath")
        if staging:
            safe_path(store.root, staging).unlink(missing_ok=True)
        with repositories.transaction():
            # Only references held by logically deleted sessions are removed.
            repositories.connection.execute(
                "DELETE FROM artifact_references WHERE artifact_id=? AND session_id IN "
                "(SELECT id FROM sessions WHERE deleted_at IS NOT NULL)", (row["id"],),
            )
            repositories.delete("artifacts", row["id"])
        deleted.append(row["id"])
    return {"dry_run": False, "candidates": preview, "deleted": deleted}
