"""Opening and inspecting an artifact uses only local facts."""

import os
import sys
import time
from datetime import UTC, datetime

import pytest

from imagen_cli.application.container import Container
from imagen_cli.application.opener import open_path
from imagen_cli.cli.entrypoint import _parse


def test_asset_show_parses_a_positional_id():
    identity = "art_" + "3" * 32
    command, args, _ = _parse(["asset", "show", identity])
    assert command == "asset show"
    assert args == {"artifact": identity}


def test_asset_show_returns_path_owners_and_no_private_staging(tmp_path, png_bytes):
    identity = "art_" + "3" * 32
    session = "ses_" + "4" * 32
    with Container(workspace=str(tmp_path / "ws")) as container:
        container.artifact_store.stage(identity, png_bytes)
        container.artifact_store.publish(identity)
        container.repositories.insert("sessions", {
            "id": session, "name": "shown", "version": 0,
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        })
        container.repositories.insert("artifact_links", {
            "session_id": session, "artifact_id": identity, "role": "output",
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        })
        status, data = container.execute("asset show", {"artifact": identity})
        assert status == "succeeded"
        assert data["available"] is True
        assert data["artifact"]["id"] == identity
        assert data["path"].endswith(".image")
        assert "staging_relpath" not in data["artifact"]
        assert data["owners"] == [{
            "session_id": session, "role": "output", "source": "link",
        }]
        missing = container.execute("asset show", {"artifact": "art_" + "9" * 32})
        assert missing[0] == "not_found"


def test_asset_open_parses_a_positional_id_and_folder_flag():
    identity = "art_" + "1" * 32
    command, args, _ = _parse(["asset", "open", identity, "--folder"])
    assert command == "asset open"
    assert args == {"artifact_id": identity, "folder": True}


def test_asset_open_launches_the_platform_handler(tmp_path, png_bytes, monkeypatch):
    if os.name == "nt":
        pytest.skip("POSIX fake handler")
    binary = tmp_path / "bin"
    binary.mkdir()
    log = tmp_path / "opened.txt"
    handler = binary / "xdg-open"
    handler.write_text('#!/bin/sh\nprintf \'%s\' "$1" > "$OPEN_LOG"\n', encoding="utf-8")
    handler.chmod(0o755)
    monkeypatch.setenv("OPEN_LOG", str(log))
    monkeypatch.setenv("PATH", f"{binary}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(sys, "platform", "linux")
    identity = "art_" + "5" * 32
    with Container(workspace=str(tmp_path / "ws")) as container:
        container.artifact_store.stage(identity, png_bytes)
        container.artifact_store.publish(identity)
        expected = str(container.artifact_store.get(identity))
        status, data = container.execute("asset open", {"artifact_id": identity})
        assert status == "succeeded" and data["opened"] == "image"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not log.exists():
        time.sleep(0.02)
    assert log.read_text(encoding="utf-8") == expected


def test_asset_open_uses_startfile_on_windows(tmp_path, png_bytes, monkeypatch):
    target = tmp_path / "image.png"
    target.write_bytes(png_bytes)
    opened: list[str] = []
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os, "startfile", lambda path: opened.append(path), raising=False)
    open_path(target)
    open_path(target, folder=True)
    assert opened == [str(target), str(target.parent)]


@pytest.mark.skipif(sys.platform == "win32", reason="uses a POSIX open handler")
def test_asset_open_uses_open_on_darwin(tmp_path, png_bytes, monkeypatch):
    binary = tmp_path / "bin"
    binary.mkdir()
    log = tmp_path / "opened.txt"
    handler = binary / "open"
    handler.write_text('#!/bin/sh\nprintf \'%s\' "$1" > "$OPEN_LOG"\n', encoding="utf-8")
    handler.chmod(0o755)
    monkeypatch.setenv("OPEN_LOG", str(log))
    monkeypatch.setenv("PATH", f"{binary}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(sys, "platform", "darwin")
    target = tmp_path / "image.png"
    target.write_bytes(png_bytes)
    open_path(target)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not log.exists():
        time.sleep(0.02)
    assert log.read_text(encoding="utf-8") == str(target)


def test_asset_open_uses_the_platform_opener(tmp_path, png_bytes, monkeypatch):
    calls = []

    def fake_open(path, *, folder):
        calls.append((path.name, folder))

    monkeypatch.setattr("imagen_cli.application.opener.open_path", fake_open)
    identity = "art_" + "1" * 32
    with Container(workspace=str(tmp_path / "ws")) as container:
        container.artifact_store.stage(identity, png_bytes)
        container.artifact_store.publish(identity)
        status, data = container.execute("asset open", {"artifact_id": identity})
        assert status == "succeeded"
        assert data["opened"] == "image"
        status, data = container.execute(
            "asset open", {"artifact_id": identity, "folder": True}
        )
        assert status == "succeeded"
        assert data["opened"] == "folder"
        missing = container.execute("asset open", {"artifact_id": "art_" + "2" * 32})
        assert missing[0] == "not_found"
    assert [folder for _, folder in calls] == [False, True]


def test_asset_delete_removes_file_and_links(tmp_path, png_bytes):
    identity = "art_" + "7" * 32
    session = "ses_" + "8" * 32
    command, args, _ = _parse(["asset", "delete", identity])
    assert command == "asset delete"
    assert args == {"artifact": identity}

    with Container(workspace=str(tmp_path / "ws")) as container:
        container.artifact_store.stage(identity, png_bytes)
        container.artifact_store.publish(identity)
        container.repositories.insert("sessions", {
            "id": session, "name": "del-test", "version": 0,
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        })
        container.repositories.insert("artifact_links", {
            "session_id": session, "artifact_id": identity, "role": "output",
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        })
        assert container.artifact_store.get(identity) is not None
        status, data = container.execute("asset delete", {"artifact": identity})
        assert status == "succeeded"
        assert data["deleted"] is True
        assert data["artifact_id"] == identity
        assert container.artifact_store.get(identity) is None
        assert container.repositories.get("artifacts", identity) is None
        assert not container.repositories.list("artifact_links", artifact_id=identity)

