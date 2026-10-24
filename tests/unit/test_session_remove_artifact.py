"""Tests for removing an artifact from a session (application service and CLI)."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from imagen_cli.application.cleanup_service import remove_session_artifact
from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.database import connect
from imagen_cli.storage.repositories import Repositories


def _setup(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    repositories = Repositories(connect(root / "state.sqlite3"))
    store = ArtifactStore(root / "artifacts", repositories)
    return root, repositories, store


def test_remove_session_artifact_links(tmp_path: Path, png_bytes: bytes):
    root, repositories, store = _setup(tmp_path)
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    session_id = "ses_" + "a" * 32

    # 1. Create a session
    repositories.insert("sessions", {"id": session_id, "name": "test-session", "version": 1})

    # 2. Stage and publish an artifact
    art_id = "art_" + "1" * 32
    store.stage(art_id, png_bytes)
    store.publish(art_id)

    # 3. Link artifact to session
    repositories.insert("artifact_links", {
        "session_id": session_id,
        "artifact_id": art_id,
        "role": "output",
        "created_at": now,
    })

    # File exists before removal
    assert store.get(art_id) is not None

    # Remove artifact from session with purge
    res = remove_session_artifact(repositories, store, session_id, art_id, purge=True)
    assert res["removed"] is True
    assert res["purged"] is True
    assert res["session_id"] == session_id
    assert res["artifact_id"] == art_id

    # Link is deleted
    links = repositories.list("artifact_links", session_id=session_id, artifact_id=art_id)
    assert len(links) == 0

    # Since it was orphan, file and artifact record are purged
    assert store.get(art_id) is None
    assert repositories.get("artifacts", art_id) is None

    repositories.connection.close()


def test_remove_session_artifact_references(tmp_path: Path, png_bytes: bytes):
    root, repositories, store = _setup(tmp_path)
    session_id = "ses_" + "b" * 32
    turn_id = "trn_" + "b" * 32

    # Create session, turn, artifact and artifact_references
    repositories.insert("sessions", {"id": session_id, "name": "sess-turn", "version": 1})

    repositories.insert("turns", {
        "id": turn_id,
        "session_id": session_id,
        "operation": "generate",
        "creation_state": "succeeded",
    })

    art_id = "art_" + "2" * 32
    store.stage(art_id, png_bytes)
    store.publish(art_id)

    repositories.insert("artifact_references", {
        "session_id": session_id,
        "turn_id": turn_id,
        "artifact_id": art_id,
        "role": "output",
        "index": 0,
    })

    # Remove
    res = remove_session_artifact(repositories, store, session_id, art_id, purge=True)
    assert res["removed"] is True
    assert res["purged"] is True

    refs = repositories.list("artifact_references", session_id=session_id, artifact_id=art_id)
    assert len(refs) == 0
    assert store.get(art_id) is None

    repositories.connection.close()


def test_remove_session_artifact_shared_not_purged(tmp_path: Path, png_bytes: bytes):
    root, repositories, store = _setup(tmp_path)
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    s1_id = "ses_" + "1" * 32
    s2_id = "ses_" + "2" * 32

    repositories.insert("sessions", {"id": s1_id, "name": "s1", "version": 1})
    repositories.insert("sessions", {"id": s2_id, "name": "s2", "version": 1})

    art_id = "art_" + "3" * 32
    store.stage(art_id, png_bytes)
    store.publish(art_id)

    repositories.insert("artifact_links", {
        "session_id": s1_id,
        "artifact_id": art_id,
        "role": "output",
        "created_at": now,
    })
    repositories.insert("artifact_links", {
        "session_id": s2_id,
        "artifact_id": art_id,
        "role": "output",
        "created_at": now,
    })

    # Remove from sess1
    res = remove_session_artifact(repositories, store, s1_id, art_id, purge=True)
    assert res["removed"] is True
    assert res["purged"] is False  # Because sess2 still references it!

    # sess1 has no links
    assert len(repositories.list("artifact_links", session_id=s1_id, artifact_id=art_id)) == 0
    # sess2 still has link
    assert len(repositories.list("artifact_links", session_id=s2_id, artifact_id=art_id)) == 1
    # File is preserved
    assert store.get(art_id) is not None

    repositories.connection.close()


def test_remove_session_artifact_not_found(tmp_path: Path):
    root, repositories, store = _setup(tmp_path)
    session_id = "ses_" + "e" * 32
    repositories.insert("sessions", {"id": session_id, "name": "empty-sess", "version": 1})
    with pytest.raises(KeyError):
        remove_session_artifact(repositories, store, session_id, "art_" + "0" * 32)
    repositories.connection.close()


def test_cli_session_remove_artifact_and_alias(tmp_path: Path, png_bytes: bytes):
    import io
    import json

    from imagen_cli.application.container import Container
    from imagen_cli.cli.entrypoint import main

    ws = tmp_path / "cli_ws"
    ws.mkdir()
    art_id = "art_" + "9" * 32
    sess_id = "ses_" + "9" * 32

    with Container(workspace=str(ws)) as c:
        c.artifact_store.stage(art_id, png_bytes)
        c.artifact_store.publish(art_id)
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        c.repositories.insert("sessions", {"id": sess_id, "name": "my-session", "version": 1})
        c.repositories.insert("artifact_links", {
            "session_id": sess_id, "artifact_id": art_id, "role": "output", "created_at": now
        })

    stdout = io.StringIO()
    code = main([
        "--workspace", str(ws),
        "session", "remove-artifact", "my-session", art_id,
        "--output", "json",
    ], stdout=stdout)
    assert code == 0
    resp = json.loads(stdout.getvalue())
    assert resp["ok"] is True
    assert resp["data"]["removed"] is True
    assert resp["data"]["artifact_id"] == art_id


