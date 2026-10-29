"""Bring an external image into a session and copy an artifact out to a chosen path."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePath
from typing import Any

from imagen_cli.domain.primitive import new_id
from imagen_cli.storage.artifacts import MAX_BYTES, ArtifactStore
from imagen_cli.storage.repositories import Repositories


def parent_of(path: PurePath) -> PurePath | None:
    """The parent directory, or None at a filesystem root (including Windows drives)."""
    parent = path.parent
    return None if parent == path else parent


def import_image(
    repositories: Repositories,
    store: ArtifactStore,
    session_id: str,
    source: str | Path,
) -> dict[str, Any]:
    """Publish one external image into a session without touching the source file."""
    session = repositories.get("sessions", session_id)
    if session is None or session.get("deleted_at") is not None:
        raise KeyError(session_id)
    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("image byte limit exceeded")
    data = path.read_bytes()
    identity = new_id("art")
    store.stage(identity, data, origin_manifest={"kind": "imported"})
    store.publish(identity)
    artifact = repositories.get("artifacts", identity)
    with repositories.transaction():
        repositories.insert(
            "artifact_links",
            {
                "session_id": session_id,
                "artifact_id": identity,
                "role": "output",
                "created_at": artifact["created_at"],
            },
        )
    return {
        "session": session_id,
        "artifact": dict(artifact),
        "path": str(store.get(identity)),
    }


def save_artifact_copy(
    repositories: Repositories,
    store: ArtifactStore,
    artifact_id: str,
    destination: str | Path,
) -> dict[str, Any]:
    """Write a verified copy to a new path; an existing file is never replaced."""
    row = repositories.get("artifacts", artifact_id)
    if row is None:
        raise KeyError(artifact_id)
    if row.get("save_state") != "published":
        raise ValueError("artifact is not published")
    source = store.get(artifact_id)
    if source is None:
        raise ValueError("artifact file is unavailable")
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != row["sha256"]:
        raise ValueError("artifact bytes do not match its record")
    target = Path(destination)
    if (
        not target.name
        or target.is_dir()
        or "\x00" in str(target)
        or not target.parent.is_dir()
    ):
        raise ValueError("destination must name a file inside an existing directory")
    fd = os.open(
        target,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return {
        "artifact": artifact_id,
        "path": str(target.absolute()),
        "bytes": len(data),
        "sha256": row["sha256"],
    }
