import io
import multiprocessing
import os
import sqlite3
import stat
import zipfile

import pytest


def test_workspace_is_explicit_or_platform_default(tmp_path, monkeypatch):
    from imagen_cli.storage.workspace import resolve_workspace

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "imagen_cli.storage.workspace.user_data_path", lambda *a, **k: tmp_path / "user"
    )
    workspace = resolve_workspace()
    assert workspace.root == tmp_path / "user"
    assert workspace.artifacts.is_dir()
    assert workspace.private.is_dir()
    assert resolve_workspace(tmp_path / "explicit").root == tmp_path / "explicit"
    if os.name == "posix":
        assert stat.S_IMODE(workspace.root.stat().st_mode) == 0o700


def test_database_pragmas_migrations_and_backup(tmp_path):
    from imagen_cli.storage.database import connect
    from imagen_cli.storage.migrations import migrate

    path = tmp_path / "state.sqlite3"
    db = connect(path)
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.execute("PRAGMA synchronous").fetchone()[0] == 2
    assert db.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert db.execute("PRAGMA user_version").fetchone()[0] == 1
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    db.close()
    # Upgrade an existing version-zero DB, including committed WAL data.
    old = sqlite3.connect(tmp_path / "old.sqlite3")
    old.execute("CREATE TABLE legacy (value TEXT)")
    old.execute("INSERT INTO legacy VALUES ('preserved')")
    old.commit()
    migrate(old)
    backup = sqlite3.connect(tmp_path / "old.sqlite3.v0.bak")
    assert backup.execute("SELECT value FROM legacy").fetchone()[0] == "preserved"
    backup.close()
    old.execute("PRAGMA user_version=999")
    with pytest.raises(ValueError, match="newer"):
        migrate(old)
    assert old.execute("PRAGMA user_version").fetchone()[0] == 999
    old.close()


def test_repository_roundtrip_relations_transactions_and_cas(tmp_path):
    from imagen_cli.storage.database import connect
    from imagen_cli.storage.repositories import Repositories

    db = connect(tmp_path / "state.sqlite3")
    repo = Repositories(db)
    repo.insert("sessions", {"id": "ses_a", "name": "one"})
    repo.insert("sessions", {"id": "ses_b", "name": "two"})
    repo.insert("turns", {"id": "trn_a", "session_id": "ses_a", "creation_state": "succeeded"})
    repo.insert("turns", {"id": "trn_b", "session_id": "ses_b"})
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        repo.insert("turns", {"id": "trn_bad", "session_id": "ses_a", "parent_turn_id": "trn_b"})
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        repo.publish_turn("ses_a", "trn_b", 0)
    assert repo.publish_turn("ses_a", "trn_a", 0)
    assert not repo.publish_turn("ses_a", "trn_a", 0)
    assert repo.get("sessions", "ses_a")["version"] == 1
    assert repo.get("turns", "trn_a")["publication_state"] == "conflict"
    with pytest.raises(RuntimeError), repo.transaction():
        repo.insert("sessions", {"id": "ses_rollback", "name": "rollback"})
        raise RuntimeError("rollback")
    assert repo.get("sessions", "ses_rollback") is None
    with pytest.raises(sqlite3.IntegrityError):
        repo.insert("sessions", {"id": "ses_duplicate", "name": "one"})
    repo.insert("jobs", {"id": "job_a", "turn_id": "trn_a"})
    repo.insert(
        "native_contexts", {"id": "ctx_a", "private_relpath": "private/secret", "sha256": "a" * 64}
    )
    repo.insert("plans", {"id": "pln_a", "session_id": "ses_a", "input_hashes": ["a" * 64]})
    repo.insert("approvals", {"id": "apr_a", "plan_id": "pln_a"})
    repo.insert("batches", {"id": "bat_a", "child_job_ids": ["job_a"], "concurrency": 2})
    repo.insert(
        "capabilities",
        {
            "id": "cap_a",
            "provider": "test",
            "family": "images",
            "model": "m",
            "operation": "generate",
            "sources": [],
        },
    )
    repo.insert("profiles", {"id": "p", "name": "profile", "credential_ref": "keyring:test"})
    repo.insert("workers", {"id": "wrk_a", "pid": 123})
    assert repo.get("plans", "pln_a")["input_hashes"] == ["a" * 64]
    assert "private_relpath" not in repo.get("native_contexts", "ctx_a")
    assert (
        repo.get("native_contexts", "ctx_a", include_private=True)["private_relpath"]
        == "private/secret"
    )
    assert repo.append_event("job_a", "started", {})["job_seq"] == 1
    assert repo.append_event("job_a", "saved", {})["job_seq"] == 2
    assert len(repo.list("events", job_id="job_a")) == 2
    with pytest.raises(ValueError):
        repo.insert("profiles", {"id": "bad", "api_key": "not-real"})
    db.close()


def test_relational_constraints_and_approval_consumption(tmp_path):
    from imagen_cli.storage.database import connect
    from imagen_cli.storage.repositories import Repositories

    db = connect(tmp_path / "state.sqlite3")
    repo = Repositories(db)
    repo.insert("sessions", {"id": "ses_a", "name": "a"})
    repo.insert("sessions", {"id": "ses_b", "name": "b"})
    repo.insert("turns", {"id": "trn_a", "session_id": "ses_a"})
    repo.insert("turns", {"id": "trn_b", "session_id": "ses_b"})
    repo.insert("jobs", {"id": "job_a", "turn_id": "trn_a"})
    repo.insert("jobs", {"id": "job_b", "turn_id": "trn_b"})
    for table, value in [
        (
            "artifacts",
            {
                "id": "art_bad",
                "producer_turn_id": "trn_a",
                "producer_job_id": "job_b",
                "managed_relpath": "bad",
            },
        ),
        ("plans", {"id": "pln_bad", "session_id": "ses_a", "parent_turn_id": "trn_b"}),
        (
            "artifact_references",
            {
                "session_id": "ses_b",
                "turn_id": "trn_a",
                "artifact_id": "missing",
                "role": "input",
                "index": 0,
            },
        ),
    ]:
        with pytest.raises((ValueError, sqlite3.IntegrityError)):
            repo.insert(table, value)
    repo.insert("batches", {"id": "bat_a", "child_job_ids": ["job_b", "job_a"], "concurrency": 2})
    assert repo.get("batches", "bat_a")["child_job_ids"] == ["job_b", "job_a"]
    assert repo.get("jobs", "job_a")["batch_id"] == "bat_a"
    repo.insert("plans", {"id": "pln_a", "session_id": "ses_a", "plan_digest": "a" * 64})
    repo.insert(
        "approvals",
        {"id": "apr_a", "plan_id": "pln_a", "plan_digest": "a" * 64, "state": "approved"},
    )
    first = repo.consume_approval(
        "apr_a",
        "a" * 64,
        {"id": "trn_exec", "session_id": "ses_a"},
        {"id": "job_exec", "turn_id": "trn_exec"},
    )
    second = repo.consume_approval(
        "apr_a",
        "a" * 64,
        {"id": "trn_other", "session_id": "ses_a"},
        {"id": "job_other", "turn_id": "trn_other"},
    )
    assert first == second == "job_exec"
    assert repo.get("turns", "trn_other") is None
    assert repo.get("approvals", "apr_a")["state"] == "consumed"
    with pytest.raises(ValueError):
        repo.consume_approval("apr_a", "b" * 64, {}, {})
    db.close()


def test_private_fields_never_enter_safe_payload(tmp_path):
    from imagen_cli.storage.database import connect
    from imagen_cli.storage.repositories import Repositories

    db = connect(tmp_path / "state.sqlite3")
    repo = Repositories(db)
    repo.insert("sessions", {"id": "ses_a", "name": "a"})
    repo.insert("turns", {"id": "trn_a", "session_id": "ses_a"})
    repo.insert("jobs", {"id": "job_a", "turn_id": "trn_a", "remote_id": "secret-remote"})
    assert "remote_id" not in repo.get("jobs", "job_a")
    for field in ("api-key", "accessToken", "download_url", "native_parts"):
        with pytest.raises(ValueError):
            repo.append_event("job_a", "unsafe", {field: "secret"})
    db.close()


def _cas_writer(path, index, queue):
    from imagen_cli.storage.database import connect
    from imagen_cli.storage.repositories import Repositories

    db = connect(path)
    repo = Repositories(db)
    turn_id = f"trn_{index}"
    repo.insert("turns", {"id": turn_id, "session_id": "ses_a", "creation_state": "succeeded"})
    queue.put(repo.publish_turn("ses_a", turn_id, 0))
    db.close()


def test_ten_process_writers_preserve_all_attempts(tmp_path):
    from imagen_cli.storage.database import connect
    from imagen_cli.storage.repositories import Repositories

    path = tmp_path / "state.sqlite3"
    db = connect(path)
    Repositories(db).insert("sessions", {"id": "ses_a", "name": "one"})
    db.close()
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    processes = [ctx.Process(target=_cas_writer, args=(path, i, queue)) for i in range(10)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(20)
        assert process.exitcode == 0
    assert sum(queue.get(timeout=5) for _ in processes) == 1
    db = connect(path)
    assert len(Repositories(db).list("turns")) == 10
    assert Repositories(db).get("sessions", "ses_a")["version"] == 1
    db.close()


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/drive", "a\\b", "a/../b", "a\x00b"])
def test_reject_unsafe_paths(tmp_path, name):
    from imagen_cli.storage.safety import safe_path

    with pytest.raises(ValueError):
        safe_path(tmp_path, name)


def test_safe_path_rejects_symlink_and_hardlink(tmp_path):
    from imagen_cli.storage.safety import safe_path

    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        safe_path(root, "link/x")
    (root / "a").write_bytes(b"a")
    os.link(root / "a", root / "b")
    with pytest.raises(ValueError):
        safe_path(root, "b")


def test_archive_limits_normalized_duplicates_links_and_secrets():
    from imagen_cli.storage.safety import validate_manifest, validate_zip

    def archive(entries):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for name, value in entries:
                z.writestr(name, value)
        data.seek(0)
        return zipfile.ZipFile(data)

    with archive([("assets/a", b"ok")]) as z:
        assert validate_zip(z) == ["assets/a"]
    for entries in [[("../a", b"x")], [("A", b"x"), ("a", b"y")], [("é", b"x"), ("e\u0301", b"y")]]:
        with archive(entries) as z, pytest.raises(ValueError):
            validate_zip(z)
    with archive([("bomb", b"0" * 10000)]) as z, pytest.raises(ValueError):
        validate_zip(z, max_file_size=100)
    info = zipfile.ZipInfo("link")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with archive([(info, b"target")]) as z, pytest.raises(ValueError):
        validate_zip(z)
    with pytest.raises(ValueError):
        validate_manifest({"artifacts": [{"id": "art_a"}, {"id": "art_a"}]})
    with pytest.raises(ValueError):
        validate_manifest({"nested": {"private_locator_ref": "secret"}})
    validate_manifest({"artifacts": [{"id": "art_a"}], "credential_ref": "keyring:ref"})


def test_private_state_exclusive_permissions_and_roundtrip(tmp_path):
    from imagen_cli.storage.private_state import PrivateStateStore

    if os.name != "posix":
        pytest.skip("POSIX permission contract")
    store = PrivateStateStore(tmp_path / "private")
    saved = store.write("ctx_a", b"opaque-native-signature")
    assert store.read("ctx_a", saved["sha256"]) == b"opaque-native-signature"
    assert stat.S_IMODE((tmp_path / "private").stat().st_mode) == 0o700
    path = tmp_path / "private" / saved["private_relpath"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        store.write("ctx_a", b"replace")
    path.chmod(0o644)
    with pytest.raises(PermissionError):
        store.read("ctx_a", saved["sha256"])
