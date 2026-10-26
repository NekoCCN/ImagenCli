"""`imagen update` reports whether a newer release exists; it installs nothing."""

from __future__ import annotations

import json
import urllib.error

import pytest

from imagen_cli.application import update_service


def release(tag: str) -> bytes:
    return json.dumps(
        {
            "tag_name": tag,
            "html_url": f"https://github.com/NekoCCN/ImagenCli/releases/tag/{tag}",
            "published_at": "2026-10-26T00:00:00Z",
        }
    ).encode()


def test_a_newer_tag_is_reported():
    status, data = update_service.check(lambda url: release("v9.9.9"))
    assert status == "succeeded"
    assert data["latest"] == "9.9.9"
    assert data["update_available"] is True


def test_the_installed_tag_is_not_an_update():
    installed = update_service.installed_version()
    status, data = update_service.check(lambda url: release(f"v{installed}"))
    assert status == "succeeded"
    assert data["update_available"] is False


def test_equal_versions_with_a_prefix_compare_numerically():
    assert update_service._parts("v0.10.0") > update_service._parts("0.9.9")
    assert update_service._parts("1.0.0-rc1") == (1, 0, 0)


def test_a_broken_feed_is_an_error_not_a_guess():
    with pytest.raises(ValueError, match="release feed"):
        update_service.check(lambda url: b"not json")
    with pytest.raises(ValueError, match="release feed"):
        update_service.check(lambda url: b"{}")


def test_release_url_tag_is_extracted_only_from_a_tag_page():
    assert (
        update_service._tag_from_release_url(
            "https://github.com/NekoCCN/ImagenCli/releases/tag/v0.1.0"
        )
        == "v0.1.0"
    )
    assert update_service._tag_from_release_url(
        "https://github.com/NekoCCN/ImagenCli/releases"
    ) is None


def test_the_cli_exposes_update(monkeypatch):
    import io

    from imagen_cli.cli.entrypoint import main

    monkeypatch.setattr(update_service, "_default_fetch", lambda url: release("v9.9.9"))
    out = io.StringIO()
    code = main(["update", "--agent", "--no-interactive"], stdout=out, stderr=io.StringIO())
    assert code == 0
    value = json.loads(out.getvalue())
    assert value["data"]["update_available"] is True


def test_network_failure_is_an_error():
    def boom(url: str) -> bytes:
        raise urllib.error.URLError("offline")

    with pytest.raises(ValueError, match="release feed"):
        update_service.check(boom)


def test_upgrade_runs_the_standard_installer(monkeypatch):
    monkeypatch.setattr(update_service, "install_method", lambda: "uv")
    calls = []

    def run(command):
        calls.append(command)
        return 0, ""

    status, data = update_service.upgrade(fetch=lambda url: release("v9.9.9"), run=run)
    assert status == "succeeded"
    assert data["upgraded"] is True
    assert calls and calls[0] == ["uv", "tool", "upgrade", "imagen-cli"]


def test_upgrade_does_nothing_when_current(monkeypatch):
    monkeypatch.setattr(update_service, "install_method", lambda: "uv")
    called = []
    status, data = update_service.upgrade(
        fetch=lambda url: release(f"v{update_service.installed_version()}"),
        run=lambda command: (called.append(command), (0, ""))[1],
    )
    assert data["upgraded"] is False
    assert called == []


def test_upgrade_refuses_an_unknown_installer(monkeypatch):
    monkeypatch.setattr(update_service, "install_method", lambda: "unknown")
    with pytest.raises(ValueError, match="uv tool or pipx"):
        update_service.upgrade(fetch=lambda url: release("v9.9.9"), run=lambda command: (0, ""))


def test_upgrade_reports_a_failed_command(monkeypatch):
    monkeypatch.setattr(update_service, "install_method", lambda: "uv")
    with pytest.raises(ValueError, match="upgrade command failed"):
        update_service.upgrade(fetch=lambda url: release("v9.9.9"), run=lambda command: (1, "boom"))


def test_install_method_detection(monkeypatch):
    import sys

    monkeypatch.setattr(sys, "executable", "/home/u/.local/share/uv/tools/imagen-cli/bin/python")
    assert update_service.install_method() == "uv"
    windows = "C:\\Users\\u\\pipx\\venvs\\imagen-cli\\Scripts\\python.exe"
    monkeypatch.setattr(sys, "executable", windows)
    assert update_service.install_method() == "pipx"
    monkeypatch.setattr(sys, "executable", "/usr/bin/python3")
    assert update_service.install_method() == "unknown"


def test_upgrade_refuses_an_unknown_installer(monkeypatch):
    monkeypatch.setattr(update_service, "install_method", lambda: "unknown")
    with pytest.raises(ValueError, match="uv tool or pipx"):
        update_service.upgrade(fetch=lambda url: release("v9.9.9"), run=lambda command: (0, ""))


def test_upgrade_reports_a_failed_command(monkeypatch):
    monkeypatch.setattr(update_service, "install_method", lambda: "uv")
    with pytest.raises(ValueError, match="upgrade command failed"):
        update_service.upgrade(fetch=lambda url: release("v9.9.9"), run=lambda command: (1, "boom"))


def test_the_cli_parses_install(monkeypatch):
    import io

    from imagen_cli.cli.entrypoint import main

    monkeypatch.setattr(
        update_service,
        "upgrade",
        lambda: ("succeeded", {"installed": "0.1.0", "latest": "9.9.9",
                               "update_available": True, "upgraded": True, "method": "uv"}),
    )
    out = io.StringIO()
    code = main(["update", "--install", "--agent", "--no-interactive"],
                stdout=out, stderr=io.StringIO())
    assert code == 0
    assert json.loads(out.getvalue())["data"]["upgraded"] is True
