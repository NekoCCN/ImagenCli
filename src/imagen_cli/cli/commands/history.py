"""Session history commands; local only, no provider or network dependency."""

import hashlib
import json
from typing import Any, cast

from imagen_cli.application.cleanup_service import collect, delete_session
from imagen_cli.application.export_service import _rows, export_session
from imagen_cli.application.fork_service import fork
from imagen_cli.application.import_service import import_archive
from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.repositories import Repositories


def _identity(repositories: Repositories, identity: str) -> str:
    if repositories.get("sessions", identity) is not None:
        return identity
    matches = [
        row for row in _rows(repositories, "sessions")
        if row["name"] == identity and row.get("deleted_at") is None
    ]
    if len(matches) != 1:
        raise KeyError(identity)
    return cast(str, matches[0]["id"])


def _gc_plan(candidates: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        [[item["id"], item["path"]] for item in sorted(candidates, key=lambda item: item["id"])],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def fork_(
    repositories: Repositories,
    session_id: str,
    *,
    turn_id: str,
    artifact_ids: tuple[str, ...] = (),
    name: str,
) -> tuple[str, dict[str, Any]]:
    try:
        session_id = _identity(repositories, session_id)
        if not artifact_ids:
            outputs = repositories.list("artifact_references", turn_id=turn_id, role="output")
            if len(outputs) != 1:
                raise ValueError("select exactly one artifact when the turn has several outputs")
            artifact_ids = (outputs[0]["artifact_id"],)
        row = fork(
            repositories, session_id, turn_id=turn_id,
            artifact_ids=artifact_ids, name=name,
        )
    except KeyError:
        return "not_found", {}
    return "succeeded", {"session": row}


def export_(
    repositories: Repositories,
    store: ArtifactStore,
    session_id: str,
    destination: str,
) -> tuple[str, dict[str, Any]]:
    try:
        data = export_session(
            repositories, store, _identity(repositories, session_id), destination,
        )
    except KeyError:
        return "not_found", {}
    except FileExistsError:
        return "conflict", {"archive": destination, "existing": True}
    return "succeeded", data


def import_(
    repositories: Repositories, store: ArtifactStore, archive: str,
) -> tuple[str, dict[str, Any]]:
    try:
        data = import_archive(repositories, store, archive)
    except FileNotFoundError:
        return "not_found", {}
    return "succeeded", data


def delete_(
    repositories: Repositories, session_id: str, *, yes: bool,
) -> tuple[str, dict[str, Any]]:
    if yes is not True:
        raise ValueError("--yes is required for session delete")
    try:
        row = delete_session(repositories, _identity(repositories, session_id))
    except KeyError:
        return "not_found", {}
    return "succeeded", {
        "session": row,
        "deleted": True,
        "exported_files_untouched": True,
    }


def gc_(
    repositories: Repositories,
    store: ArtifactStore,
    *,
    dry_run: bool,
    plan: str | None,
    yes: bool,
) -> tuple[str, dict[str, Any]]:
    preview = collect(repositories, store)
    digest = _gc_plan(preview["candidates"])
    if dry_run:
        return "succeeded", {
            "dry_run": True,
            "plan": digest,
            "candidates": preview["candidates"],
            "deleted": [],
        }
    if yes is not True or plan is None:
        raise ValueError("--plan and --yes are required for destructive GC")
    if plan != digest:
        return "conflict", {
            "dry_run": False,
            "plan": digest,
            "candidates": preview["candidates"],
            "deleted": [],
        }
    # Recheck every reference inside collect before deleting the confirmed set.
    confirmed = tuple(sorted(item["id"] for item in preview["candidates"]))
    applied = collect(repositories, store, confirmed=confirmed)
    return "succeeded", {
        "dry_run": False,
        "plan": digest,
        "candidates": preview["candidates"],
        "deleted": applied["deleted"],
    }
