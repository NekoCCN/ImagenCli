"""TUI display labels follow the selected interface language."""

from __future__ import annotations

from pathlib import Path

import pytest

from imagen_cli import i18n

pytest.importorskip("textual")

from imagen_cli.cli.tui.app import TransformScreen, _label  # noqa: E402


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("IMAGEN_LANG", raising=False)
    i18n.reset_language()
    yield
    i18n.reset_language()


def test_english_labels_are_default():
    assert _label("prompt") == "Prompt"
    screen = TransformScreen(None, "art_test", "ses_test", Path("missing.png"))
    assert screen.categories[0] == "All"
    assert "experimental" in str(screen.header_title.render())


def test_chinese_labels_with_environment(monkeypatch):
    monkeypatch.setenv("IMAGEN_LANG", "zh-CN")
    assert _label("prompt") == "提示词"
    screen = TransformScreen(None, "art_test", "ses_test", Path("missing.png"))
    assert screen.categories[0] == "全部"
