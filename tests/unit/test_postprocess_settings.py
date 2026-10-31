"""Deadline and working-memory limits are user settings, not hard-coded walls."""

import json

import pytest
from PIL import Image

from imagen_cli import i18n, settings
from imagen_cli.application import postprocess_service, postprocess_worker


def test_adaptive_timeout_is_the_default():
    pipeline = [{"op": "invert", "params": {}}]
    assert postprocess_worker.default_timeout(pipeline, 4_000_000) > 120


def test_timeout_can_be_overridden_by_environment(monkeypatch):
    monkeypatch.setenv("IMAGEN_POSTPROCESS_TIMEOUT", "600")
    pipeline = [{"op": "rtv_smooth", "params": {}}]
    assert postprocess_worker.default_timeout(pipeline, 8_000_000) == 600


@pytest.mark.parametrize("value", ["0", "off", "none", "unlimited"])
def test_timeout_can_be_disabled(monkeypatch, value):
    monkeypatch.setenv("IMAGEN_POSTPROCESS_TIMEOUT", value)
    assert postprocess_worker.default_timeout([{"op": "rtv_smooth"}], 8_000_000) == 0


def test_timeout_can_come_from_the_config_file(monkeypatch, tmp_path):
    monkeypatch.delenv("IMAGEN_POSTPROCESS_TIMEOUT", raising=False)
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"postprocess_timeout_seconds": 42}), encoding="utf-8")
    monkeypatch.setattr(i18n, "config_path", lambda: path)
    assert settings.timeout_limit() == 42


def test_invalid_timeout_setting_is_rejected(monkeypatch):
    monkeypatch.setenv("IMAGEN_POSTPROCESS_TIMEOUT", "soon")
    with pytest.raises(ValueError, match="IMAGEN_POSTPROCESS_TIMEOUT"):
        postprocess_worker.default_timeout([{"op": "invert"}], 1_000_000)


def test_memory_limit_can_be_raised(monkeypatch):
    pipeline = [{"op": "kuwahara", "params": {}}]
    with pytest.raises(ValueError, match="working memory budget"):
        postprocess_service.check_budget((2500, 2500), pipeline)
    monkeypatch.setenv("IMAGEN_POSTPROCESS_MEMORY_MB", "4096")
    postprocess_service.check_budget((2500, 2500), pipeline)


def test_memory_limit_can_be_disabled(monkeypatch):
    monkeypatch.setenv("IMAGEN_POSTPROCESS_MEMORY_MB", "unlimited")
    postprocess_service.check_budget((4000, 4000), [{"op": "kuwahara", "params": {}}])
    assert postprocess_worker._address_space_extra([{"op": "kuwahara"}]) is None


def test_worker_accepts_an_unlimited_deadline(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (8, 8), (10, 20, 30)).save(source)
    _, metadata = postprocess_worker.execute(
        source, [{"op": "invert", "params": {}}], timeout=0
    )
    assert metadata["execution"]["timeout_seconds"] == 0


def test_saving_the_language_keeps_other_config_keys(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"postprocess_memory_mb": 4096}), encoding="utf-8")
    monkeypatch.setattr(i18n, "config_path", lambda: path)
    i18n.save_language("zh-CN")
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "postprocess_memory_mb": 4096,
        "language": "zh-CN",
    }
