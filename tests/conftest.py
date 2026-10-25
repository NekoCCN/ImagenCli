"""Shared offline test fixtures."""

import pytest

pytest_plugins = ["tests.fixtures.offline"]


@pytest.fixture(autouse=True)
def _chinese_ui_for_legacy_tests(monkeypatch, tmp_path):
    """Existing assertions were written against the Chinese interface.

    I18n tests override this by clearing ``IMAGEN_LANG`` or passing ``--lang``.
    """
    monkeypatch.setenv("IMAGEN_LANG", "zh-CN")
    from imagen_cli import i18n

    monkeypatch.setattr(i18n, "config_path", lambda: tmp_path / "config" / "config.json")
    i18n.reset_language()
    yield
    i18n.reset_language()
