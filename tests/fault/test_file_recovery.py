import hashlib
import io
import multiprocessing
from pathlib import Path

import pytest
from PIL import Image


def png():
    data = io.BytesIO()
    Image.new("RGB", (2, 3), "red").save(data, format="PNG")
    return data.getvalue()


def setup_store(root):
    from imagen_cli.storage.artifacts import ArtifactStore
    from imagen_cli.storage.database import connect
    from imagen_cli.storage.repositories import Repositories

    db = connect(root / "state.sqlite3")
    return db, Repositories(db), ArtifactStore(root / "artifacts", Repositories(db))


def test_stage_publish_verifies_media_and_never_overwrites(tmp_path):
    db, repo, store = setup_store(tmp_path)
    data = png()
    row = store.stage("art_a", data)
    assert row["save_state"] == "staging"
    assert store.get("art_a") is None
    published = store.publish("art_a")
    assert published["save_state"] == "published"
    assert (published["width"], published["height"], published["media_type"]) == (2, 3, "image/png")
    assert published["sha256"] == hashlib.sha256(data).hexdigest()
    assert store.get("art_a").read_bytes() == data
    with pytest.raises((FileExistsError, ValueError)):
        store.stage("art_a", data)
    with pytest.raises(ValueError):
        store.stage("art_bad", b"not an image")
    assert repo.get("artifacts", "art_bad") is None
    db.close()


def _stage_and_exit(root):
    import os

    db, repo, store = setup_store(Path(root))
    store.stage("art_crash", png())
    os._exit(0)


def test_process_crash_after_staging_can_reconcile(tmp_path):
    process = multiprocessing.get_context("spawn").Process(
        target=_stage_and_exit, args=(str(tmp_path),)
    )
    process.start()
    process.join(20)
    assert process.exitcode == 0
    db, repo, store = setup_store(tmp_path)
    assert store.get("art_crash") is None
    assert store.reconcile("art_crash")["save_state"] == "published"
    assert store.get("art_crash").read_bytes() == png()
    db.close()


def test_complete_target_before_metadata_commit_is_recovered(tmp_path):
    db, repo, store = setup_store(tmp_path)
    row = store.stage("art_a", png())
    temporary = store.root / row["staging_relpath"]
    target = store.root / row["managed_relpath"]
    target.write_bytes(temporary.read_bytes())
    temporary.unlink()
    assert store.reconcile("art_a")["save_state"] == "published"
    db.close()


def test_corrupt_or_missing_staging_fails_without_reconstruction(tmp_path):
    db, repo, store = setup_store(tmp_path)
    row = store.stage("art_a", png())
    (store.root / row["staging_relpath"]).write_bytes(b"corrupt")
    assert store.reconcile("art_a")["save_state"] == "save_failed"
    assert store.get("art_a") is None
    row = store.stage("art_b", png())
    (store.root / row["staging_relpath"]).unlink()
    assert store.reconcile("art_b")["save_state"] == "save_failed"
    orphan = store.root / "art_orphan.png"
    orphan.write_bytes(png())
    with pytest.raises(KeyError):
        store.reconcile("art_orphan")
    assert orphan.read_bytes() == png()
    db.close()


def test_metadata_failure_preserves_staging_and_existing_destination(tmp_path, monkeypatch):
    db, repo, store = setup_store(tmp_path)
    row = store.stage("art_a", png())
    target = store.root / row["managed_relpath"]
    target.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        store.publish("art_a")
    assert target.read_bytes() == b"existing"
    assert repo.get("artifacts", "art_a")["save_state"] == "staging"
    db.close()
