"""Incremental schema upgrades: v1 workspaces gain the v2 tables intact."""

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

    assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    assert {"provider_models", "artifact_links"} <= tables(connection)
    assert connection.execute("SELECT id FROM sessions").fetchall() == [("ses_x",)]


def test_fresh_database_is_created_at_current_version(tmp_path):
    connection = sqlite3.connect(tmp_path / "state.sqlite3")
    migrations.migrate(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
    assert {"provider_models", "artifact_links"} <= tables(connection)


def test_newer_database_is_refused(tmp_path):
    connection = sqlite3.connect(tmp_path / "state.sqlite3")
    connection.execute("PRAGMA user_version=99")
    try:
        migrations.migrate(connection)
    except ValueError as error:
        assert "newer" in str(error)
    else:  # pragma: no cover - the refusal is the assertion
        raise AssertionError("newer schema must be refused")
