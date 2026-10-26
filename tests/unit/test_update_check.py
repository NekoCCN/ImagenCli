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
