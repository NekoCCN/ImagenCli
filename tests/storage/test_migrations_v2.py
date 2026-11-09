"""Incremental schema upgrades: old workspaces gain every later table intact."""

import sqlite3

from imagen_cli.storage import migrations


def tables(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def test_v1_workspace_upgrades_without_data_loss(tmp_path):
    connection = sqlite3.connect(tmp_path / "state.sqlite3")
    connection.executescript(migrations.SCHEMA + "\nPRAGMA user_version=1;")
    connection.execute("INSERT INTO sessions (id,name) VALUES ('ses_x','x')")
    connection.commit()

    migrations.migrate(connection)

    assert connection.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
    assert {"provider_models", "artifact_links", "remote_jobs"} <= tables(connection)
    assert connection.execute("SELECT id FROM sessions").fetchall() == [("ses_x",)]


def test_fresh_database_is_created_at_current_version(tmp_path):
    connection = sqlite3.connect(tmp_path / "state.sqlite3")
    migrations.migrate(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
    assert {"provider_models", "artifact_links", "remote_jobs"} <= tables(connection)


def test_v3_remote_job_artifact_ids_survive_upgrade_without_foreign_keys(tmp_path):
    connection = sqlite3.connect(tmp_path / "state.sqlite3")
    migrations.migrate(connection)
    connection.execute("INSERT INTO artifacts (id,managed_relpath) VALUES ('art_in','in.image')")
    connection.execute("INSERT INTO artifacts (id,managed_relpath) VALUES ('art_out','out.image')")
    connection.execute(
        "INSERT INTO remote_jobs "
        "(id,provider,endpoint_host,operation,input_artifact_id,input_sha256,"
        "request_canonical,job_digest,max_seconds,status,artifact_id,created_at,updated_at) "
        "VALUES ('rmt_x','modal','worker.example','matte','art_in','sha','{}',"
        "'digest',60,'succeeded','art_out','now','now')"
    )
    connection.commit()
    connection.execute("PRAGMA user_version=3")
    connection.commit()

    migrations.migrate(connection)

    assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
    assert connection.execute(
        "SELECT input_artifact_id,artifact_id FROM remote_jobs WHERE id='rmt_x'"
    ).fetchone() == ("art_in", "art_out")
    foreign_keys = connection.execute("PRAGMA foreign_key_list(remote_jobs)").fetchall()
    assert not any(row[2] == "artifacts" for row in foreign_keys)


def test_newer_database_is_refused(tmp_path):
    connection = sqlite3.connect(tmp_path / "state.sqlite3")
    connection.execute("PRAGMA user_version=99")
    try:
        migrations.migrate(connection)
    except ValueError as error:
        assert "newer" in str(error)
    else:  # pragma: no cover - the refusal is the assertion
        raise AssertionError("newer schema must be refused")
