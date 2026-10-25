"""Registry collisions and broken optional modules cannot disappear silently."""

from types import SimpleNamespace

import pytest

from imagen_cli.application import media_ops


def test_duplicate_optional_id_is_rejected(monkeypatch):
    monkeypatch.setattr("importlib.import_module", lambda name: SimpleNamespace(
        OPS={"collision": media_ops.blur, "kuwahara": media_ops.kuwahara}))
    with pytest.raises(ValueError, match="duplicate.*collision"):
        media_ops._optional_ops()


def test_internal_import_error_is_not_reported_as_missing_extra(monkeypatch):
    def broken(name):
        raise ImportError("broken module implementation")

    monkeypatch.setattr("importlib.import_module", broken)
    with pytest.raises(ImportError, match="broken module implementation"):
        media_ops._optional_ops()


def test_missing_numpy_is_optional(monkeypatch):
    def unavailable(name):
        raise ModuleNotFoundError("No module named numpy", name="numpy")

    monkeypatch.setattr("importlib.import_module", unavailable)
    assert media_ops._optional_ops() == {}
