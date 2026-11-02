"""superres doctor proves a backend with one tile and cleans its scratch directory."""

import tempfile
from pathlib import Path

import pytest
from PIL import Image

from imagen_cli.application import superres
from imagen_cli.cli.entrypoint import _parse
from imagen_cli.cli.registry import discover

PINNED = "realesr-animevideov3"


def test_an_explicit_unavailable_backend_reports_missing(monkeypatch):
    monkeypatch.setattr(superres, "available_providers", lambda: ["CPUExecutionProvider"])
    data = superres.doctor(backend="cuda")
    assert data["status"] == "missing"
    assert data["fallback"] is False
    assert data["provider_used"] is None
    assert "CUDAExecutionProvider" in data["missing"]


def test_auto_reports_the_fallback_it_chose(monkeypatch, tmp_path):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(superres, "available_providers", lambda: ["CPUExecutionProvider"])
    data = superres.doctor(backend="auto")
    assert data["backend"] == "cpu"
    assert data["fallback"] is True
    assert data["status"] == "ready"
    assert data["inference"] is False


def test_a_ready_model_runs_one_tile_and_reports_it(monkeypatch, tmp_path):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(superres, "available_providers", lambda: ["CPUExecutionProvider"])
    superres.weights_path(PINNED).write_bytes(b"weights")

    def fake_upscale(image, **kwargs):
        assert image.size == (32, 32)
        return Image.new("RGB", (128, 128), (1, 2, 3)), {
            "provider": "CPUExecutionProvider", "weights_sha256": "a" * 64,
        }

    monkeypatch.setattr(superres, "upscale", fake_upscale)
    data = superres.doctor(PINNED, backend="cpu")
    assert data["status"] == "ready"
    assert data["inference"] is True
    assert data["model"] == PINNED
    assert data["provider_used"] == "CPUExecutionProvider"
    assert data["weights_verified"] is True
    assert data["latency_ms"] is not None
    assert len(data["output_sha256"]) == 64


def test_missing_weights_do_not_download_and_stay_runtime_only(monkeypatch, tmp_path):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(superres, "available_providers", lambda: ["CPUExecutionProvider"])
    monkeypatch.setattr(superres, "upscale", lambda *a, **k: pytest.fail("downloaded"))
    data = superres.doctor(backend="cpu")
    assert data["model"] is None
    assert data["inference"] is False
    assert not list(tmp_path.glob("*.onnx"))


def test_the_scratch_directory_is_removed_on_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(superres, "available_providers", lambda: ["CPUExecutionProvider"])
    superres.weights_path(PINNED).write_bytes(b"weights")
    created = []
    real = tempfile.TemporaryDirectory

    def tracked(*args, **kwargs):
        directory = real(*args, **kwargs)
        created.append(Path(directory.name))
        return directory

    monkeypatch.setattr(superres.tempfile, "TemporaryDirectory", tracked)
    monkeypatch.setattr(
        superres, "upscale", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom"))
    )
    with pytest.raises(ValueError, match="boom"):
        superres.doctor(PINNED, backend="cpu")
    assert created and not created[0].exists()


def test_doctor_is_registered_with_both_backends():
    schema = discover("superres doctor")["input_schema"]
    assert schema["properties"]["backend"]["enum"] == ["auto", "cpu", "cuda", "openvino"]
    assert "model" not in schema.get("required", ())
    command, args, _ = _parse(["superres", "doctor", "--backend", "cuda"])
    assert command == "superres doctor"
    assert args == {"backend": "cuda"}
