"""Connections are process-owned and must be opened again after fork."""

import os
import sqlite3
from pathlib import Path
from typing import Any

from imagen_cli.storage.migrations import migrate
from imagen_cli.storage.safety import safe_path, secure_directory


class ProcessConnection(sqlite3.Connection):
    owner_pid: int

    def assert_owner(self) -> None:
        if self.owner_pid != os.getpid():
            raise RuntimeError("SQLite connection cannot be shared across processes")

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        self.assert_owner()
        return super().execute(sql, parameters)

    def executescript(self, sql_script: str, /) -> sqlite3.Cursor:
        self.assert_owner()
        return super().executescript(sql_script)


def connect(path: str | Path) -> ProcessConnection:
    path = Path(path).absolute()
    secure_directory(path.parent)
    safe_path(path.parent, path.name)
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
    os.close(fd)
    if os.name == "posix":
        path.chmod(0o600)
    connection = sqlite3.connect(path, timeout=5, isolation_level=None, factory=ProcessConnection)
    connection.owner_pid = os.getpid()
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        migrate(connection)
    except BaseException:
        connection.close()
        raise
    return connection
