from __future__ import annotations

import pytest

from imagen_cli import i18n


@pytest.fixture(autouse=True)
def _isolated_language(monkeypatch):
    monkeypatch.delenv("IMAGEN_LANG", raising=False)
    i18n.reset_language()
    yield
    i18n.reset_language()


def test_default_language_is_english():
    assert i18n.language() == "en"
    assert i18n.t("File", "文件") == "File"


def test_environment_language(monkeypatch):
    monkeypatch.setenv("IMAGEN_LANG", "zh-CN")
    assert i18n.language() == "zh-CN"
    assert i18n.t("File", "文件") == "文件"


def test_environment_aliases_and_unknown(monkeypatch):
    monkeypatch.setenv("IMAGEN_LANG", "zh")
    assert i18n.language() == "zh-CN"
    monkeypatch.setenv("IMAGEN_LANG", "fr")
    assert i18n.language() == "en"


def test_explicit_language_wins_over_environment(monkeypatch):
    monkeypatch.setenv("IMAGEN_LANG", "zh-CN")
    token = i18n.set_language("en")
    try:
        assert i18n.language() == "en"
    finally:
        i18n.reset_language(token)


def test_unknown_explicit_language_is_rejected():
    with pytest.raises(ValueError):
        i18n.set_language("fr")


def test_takes_language_argument():
    remaining, value = i18n.take_argument(["session", "list", "--lang", "zh-CN"])
    assert remaining == ["session", "list"]
    assert value == "zh-CN"
    remaining, value = i18n.take_argument(["ops", "--lang=en"])
    assert remaining == ["ops"]
    assert value == "en"


def test_rejects_missing_or_duplicate_language():
    with pytest.raises(ValueError):
        i18n.take_argument(["ops", "--lang"])
    with pytest.raises(ValueError):
        i18n.take_argument(["--lang=zh-CN", "--lang=en"])


def test_short_language_flags():
    remaining, value = i18n.take_argument(["help", "--zh"])
    assert remaining == ["help"]
    assert value == "zh-CN"
    remaining, value = i18n.take_argument(["--en", "tui"])
    assert remaining == ["tui"]
    assert value == "en"


def test_language_flags_conflict():
    with pytest.raises(ValueError):
        i18n.take_argument(["--zh", "--en"])
    with pytest.raises(ValueError):
        i18n.take_argument(["--zh", "--lang", "en"])


def test_saved_language_is_used_without_env_or_flag(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    i18n.save_language("zh-CN")
    i18n.reset_language()
    assert i18n.language() == "zh-CN"


def test_environment_overrides_saved_language(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    i18n.save_language("zh-CN")
    monkeypatch.setenv("IMAGEN_LANG", "en")
    i18n.reset_language()
    assert i18n.language() == "en"


def test_invalid_saved_language_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    config = i18n.config_path()
    config.parent.mkdir(parents=True)
    config.write_text('{"language": "fr"}', encoding="utf-8")
    i18n.reset_language()
    assert i18n.language() == "en"


def test_save_language_rejects_unknown():
    with pytest.raises(ValueError):
        i18n.save_language("fr")
