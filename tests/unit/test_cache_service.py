"""Cache audit and purge: exclusive kinds, exact bytes, digest gate."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from imagen_cli.application import cache_service
from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.database import connect
from imagen_cli.storage.repositories import Repositories

NOW = datetime(2026, 10, 24, tzinfo=UTC)
OLD = (NOW - timedelta(days=30)).isoformat().replace("+00:00", "Z")
FRESH = (NOW - timedelta(days=1)).isoformat().replace("+00:00", "Z")


def artifact_id(seed: int) -> str:
    return f"art_{seed:032x}"


@pytest.fixture
def store(tmp_path: Path, png_bytes: bytes):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repositories = Repositories(connect(workspace / "state.sqlite3"))
    store = ArtifactStore(workspace / "artifacts", repositories)
    repositories.insert(
        "sessions",
        {"id": "ses_live", "name": "live", "version": 0, "created_at": OLD},
    )
    repositories.insert(
        "sessions",
        {"id": "ses_gone", "name": "gone", "version": 0, "created_at": OLD, "deleted_at": OLD},
    )
    yield repositories, store, png_bytes
    repositories.connection.close()


def publish(store: ArtifactStore, seed: int, data: bytes) -> str:
    identity = artifact_id(seed)
    store.stage(identity, data)
    store.publish(identity)
    return identity


def age(repositories: Repositories, identity: str, value: str) -> None:
    repositories.update("artifacts", identity, {"created_at": value})


def link(repositories: Repositories, session: str, identity: str) -> None:
    repositories.insert(
        "artifact_links",
        {"session_id": session, "artifact_id": identity, "role": "output", "created_at": OLD},
    )


def test_staging_artifact_is_reclaimed_with_its_files(store):
    repositories, artifacts, data = store
    identity = artifact_id(1)
    artifacts.stage(identity, data)
    age(repositories, identity, OLD)

    entries = cache_service.audit(repositories, artifacts, now=NOW)
    assert [entry.kind for entry in entries] == ["staging_artifact"]
    assert entries[0].bytes == len(data)

    report = cache_service.report(entries)
    result = cache_service.purge(repositories, artifacts, report["plan_digest"], clock=lambda: NOW)
    assert result["deleted_bytes"] == len(data)
    assert repositories.get("artifacts", identity) is None
    assert not (artifacts.root / f"{identity}.staging").exists()


def test_fresh_staging_is_kept(store):
    repositories, artifacts, data = store
    identity = artifact_id(2)
    artifacts.stage(identity, data)
    age(repositories, identity, FRESH)
    assert cache_service.audit(repositories, artifacts, now=NOW) == []


def test_unreferenced_published_artifact(store):
    repositories, artifacts, data = store
    publish(artifacts, 3, data)
    entries = cache_service.audit(repositories, artifacts, now=NOW)
    assert [entry.kind for entry in entries] == ["unreferenced_artifact"]
    assert entries[0].bytes > 0


def test_live_reference_keeps_artifact(store):
    repositories, artifacts, data = store
    identity = publish(artifacts, 4, data)
    link(repositories, "ses_live", identity)
    assert cache_service.audit(repositories, artifacts, now=NOW) == []


def test_deleted_session_artifact_respects_retention(store):
    repositories, artifacts, data = store
    identity = publish(artifacts, 5, data)
    link(repositories, "ses_gone", identity)
    entries = cache_service.audit(repositories, artifacts, now=NOW)
    assert [entry.kind for entry in entries] == ["deleted_session_artifact"]
    young = cache_service.audit(
        repositories, artifacts, now=NOW, deleted_days=90
    )
    assert young == []


def test_expired_approval_and_plan_are_row_only(store):
    repositories, artifacts, _ = store
    repositories.insert(
        "plans",
        {"id": "pln_" + "a" * 32, "session_id": "ses_live", "expires_at": OLD},
    )
    repositories.insert(
        "approvals",
        {"id": "apr_" + "a" * 32, "plan_id": "pln_" + "a" * 32, "expires_at": OLD},
    )
    entries = cache_service.audit(repositories, artifacts, now=NOW)
    kinds = {entry.kind for entry in entries}
    assert kinds == {"expired_approval"}
    assert entries[0].bytes == 0


def test_digest_mismatch_refuses_everything(store):
    repositories, artifacts, data = store
    identity = publish(artifacts, 6, data)
    with pytest.raises(ValueError, match="digest"):
        cache_service.purge(repositories, artifacts, "0" * 64, clock=lambda: NOW)
    assert repositories.get("artifacts", identity) is not None


def test_concurrent_reference_blocks_purge(store):
    repositories, artifacts, data = store
    identity = publish(artifacts, 7, data)
    digest = cache_service.report(
        cache_service.audit(repositories, artifacts, now=NOW)
    )["plan_digest"]
    link(repositories, "ses_live", identity)
    with pytest.raises(ValueError, match="digest"):
        cache_service.purge(repositories, artifacts, digest, clock=lambda: NOW)
    assert repositories.get("artifacts", identity) is not None
