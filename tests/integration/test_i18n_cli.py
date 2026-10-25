"""English is the default UI language; ``--lang`` and ``IMAGEN_LANG`` select Chinese."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from imagen_cli import i18n
from imagen_cli.cli.entrypoint import main


@pytest.fixture(autouse=True)
def _english(monkeypatch):
    monkeypatch.delenv("IMAGEN_LANG", raising=False)
    i18n.reset_language()
    yield
    i18n.reset_language()


def invoke(tmp_path: Path, *args: str) -> tuple[int, str]:
    out = io.StringIO()
    err = io.StringIO()
    code = main(["--workspace", str(tmp_path / "ws"), *args], stdout=out, stderr=err)
    return code, out.getvalue()


def test_help_defaults_to_english(tmp_path):
    _, text = invoke(tmp_path, "help")
    assert "查看命令帮助" not in text
    assert "Show command help" in text


def test_lang_flag_selects_chinese_help(tmp_path):
    _, text = invoke(tmp_path, "--lang", "zh-CN", "help")
    assert "查看命令帮助" in text


def test_environment_selects_chinese_help(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEN_LANG", "zh-CN")
    _, text = invoke(tmp_path, "help")
    assert "查看命令帮助" in text


def test_validation_error_is_english_by_default(tmp_path):
    code, text = invoke(tmp_path, "not-a-command")
    assert code == 2
    assert text.startswith("Failed:")
    assert "status: validation_error" in text


def test_validation_error_is_chinese_with_lang(tmp_path):
    _, text = invoke(tmp_path, "--lang", "zh-CN", "not-a-command")
    assert text.startswith("失败：")


def test_lang_flag_overrides_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEN_LANG", "zh-CN")
    _, text = invoke(tmp_path, "--lang", "en", "help")
    assert "Show command help" in text
    assert "查看命令帮助" not in text


def test_operation_intro_is_english_by_default(tmp_path):
    _, text = invoke(tmp_path, "ops", "--op", "guided_smooth")
    assert "操作：" not in text
    assert "Operation:" in text
    assert "Guided edge-preserving smoothing" in text
    assert "引导保边平滑" not in text


def test_operation_intro_is_chinese_with_lang(tmp_path):
    _, text = invoke(tmp_path, "--lang=zh-CN", "ops", "--op", "guided_smooth")
    assert "操作：" in text
    assert "引导保边平滑" in text


def test_operation_catalog_lists_english_names(tmp_path):
    _, text = invoke(tmp_path, "ops")
    assert "experimental" in text
    assert "Pixelate" in text
    # Preset keys stay unchanged for compatibility; only display names translate.
    assert "  像素化\n" not in text


def test_unknown_language_is_a_validation_error(tmp_path):
    code, text = invoke(tmp_path, "--lang", "fr", "help")
    assert code == 2
    assert "status: validation_error" in text


def test_short_flag_selects_chinese(tmp_path):
    _, text = invoke(tmp_path, "--zh", "help")
    assert "查看命令帮助" in text


def test_short_flag_overrides_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEN_LANG", "zh-CN")
    _, text = invoke(tmp_path, "--en", "help")
    assert "Show command help" in text


def test_language_command_persists_default(tmp_path):
    code, text = invoke(tmp_path, "language", "zh-CN")
    assert code == 0
    assert "zh-CN" in text
    _, help_text = invoke(tmp_path, "help")
    assert "查看命令帮助" in help_text


def test_language_command_shows_saved_default(tmp_path):
    invoke(tmp_path, "language", "zh-CN")
    i18n.reset_language()
    code, text = invoke(tmp_path, "language")
    assert code == 0
    assert "zh-CN" in text
