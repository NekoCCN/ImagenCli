"""fp16 derivation: cache, fingerprint, atomic write, and the auto-retry rule."""

import hashlib
import json

import numpy as np
import pytest
from PIL import Image

from imagen_cli.application import matting, matting_fp16


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def weights(tmp_path, monkeypatch):
    source = tmp_path / "birefnet-lite.onnx"
    source.write_bytes(b"fake-onnx-weights")
    monkeypatch.setattr(matting_fp16, "_weights_path", lambda model: source)
    monkeypatch.setattr(matting_fp16, "_manifest_digest", lambda model: _sha(source))
    return source


def _converter(sink):
    def convert(source, target):
        sink.append((source, target))
        target.write_bytes(b"fp16-weights")
    return convert


def test_ensure_fp16_derives_once_then_reuses_the_cache(weights):
    conversions = []
    first, info = matting_fp16.ensure_fp16("birefnet-lite", convert=_converter(conversions))
    assert first.read_bytes() == b"fp16-weights"
    assert info["derived"] is True
    assert len(conversions) == 1

    second, again = matting_fp16.ensure_fp16("birefnet-lite", convert=_converter(conversions))
    assert second == first
    assert again["derived"] is False
    assert len(conversions) == 1


def test_ensure_fp16_rederives_when_the_fingerprint_changes(weights):
    conversions = []
    path, _ = matting_fp16.ensure_fp16("birefnet-lite", convert=_converter(conversions))
    fingerprint = json.loads(matting_fp16.fingerprint_path("birefnet-lite").read_text())
    assert fingerprint["source_sha256"] == _sha(weights)
    assert fingerprint["keep_io_types"] is True
    fingerprint["onnxruntime"] = "0.0.0-not-this-one"
    matting_fp16.fingerprint_path("birefnet-lite").write_text(json.dumps(fingerprint))

    again, info = matting_fp16.ensure_fp16("birefnet-lite", convert=_converter(conversions))
    assert again == path
    assert info["derived"] is True
    assert len(conversions) == 2


def test_ensure_fp16_refuses_when_the_source_digest_does_not_match(weights, monkeypatch):
    monkeypatch.setattr(matting_fp16, "_manifest_digest", lambda model: "0" * 64)
    with pytest.raises(ValueError, match="digest"):
        matting_fp16.ensure_fp16("birefnet-lite", convert=_converter([]))


def test_ensure_fp16_never_leaves_a_partial_file(weights):
    def exploding(source, target):
        target.write_bytes(b"half")
        raise RuntimeError("conversion blew up")

    with pytest.raises(RuntimeError, match="blew up"):
        matting_fp16.ensure_fp16("birefnet-lite", convert=exploding)
    assert not matting_fp16.derived_path("birefnet-lite").exists()
    assert not matting_fp16.fingerprint_path("birefnet-lite").exists()


def test_ensure_fp16_reports_a_missing_converter_as_unsupported(weights, monkeypatch):
    def missing(source, target):
        raise ImportError("no onnxconverter_common here")

    monkeypatch.setattr(matting_fp16, "_default_convert", missing)
    with pytest.raises(matting_fp16.UnsupportedInThisImage, match="onnxconverter"):
        matting_fp16.ensure_fp16("birefnet-lite")


def _fake_runner_factory(calls, *, oom_on_fp32=True):
    def make(model, *, backend, accept, precision=None, **_):
        calls.append(precision)

        def run(tile):
            if oom_on_fp32 and precision != "fp16":
                raise ValueError("backend cuda ran out of memory while running the model")
            width, height = tile.size
            return np.zeros((height, width), dtype=np.float32)

        return run, {"precision": precision or "fp32", "provider": "CUDAExecutionProvider"}

    return make


def test_auto_precision_retries_fp16_once_on_out_of_memory(monkeypatch):
    calls = []
    monkeypatch.setattr(matting, "_make_runner",
                        _fake_runner_factory(calls, oom_on_fp32=True))
    result, engine = matting.matte(Image.new("RGB", (16, 16)), backend="cuda")
    assert calls == [None, "fp16"]
    assert engine["precision"] == "fp16"
    assert result.mode == "RGBA"


def test_auto_precision_does_not_retry_twice(monkeypatch):
    calls = []

    def make(model, *, backend, accept, precision=None, **_):
        calls.append(precision)

        def run(tile):
            raise ValueError("backend cuda ran out of memory while running the model")

        return run, {"precision": precision or "fp32"}

    monkeypatch.setattr(matting, "_make_runner", make)
    with pytest.raises(ValueError, match="out of memory"):
        matting.matte(Image.new("RGB", (16, 16)), backend="cuda")
    assert calls == [None, "fp16"]


def test_explicit_fp16_never_tries_fp32_first(monkeypatch):
    calls = []
    monkeypatch.setattr(matting, "_make_runner",
                        _fake_runner_factory(calls, oom_on_fp32=True))
    _, engine = matting.matte(Image.new("RGB", (16, 16)),
                              backend="cuda", precision="fp16")
    assert calls == ["fp16"]
    assert engine["precision"] == "fp16"


def test_explicit_fp32_never_falls_back(monkeypatch):
    calls = []
    monkeypatch.setattr(matting, "_make_runner",
                        _fake_runner_factory(calls, oom_on_fp32=True))
    with pytest.raises(ValueError, match="out of memory"):
        matting.matte(Image.new("RGB", (16, 16)), backend="cuda", precision="fp32")
    assert calls == ["fp32"]


def test_an_injected_runner_is_left_alone(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("a provided runner must be used as-is")

    monkeypatch.setattr(matting, "_make_runner", fail)

    def runner(tile):
        width, height = tile.size
        return np.zeros((height, width), dtype=np.float32)

    result, engine = matting.matte(Image.new("RGB", (8, 8)), runner=runner)
    assert result.mode == "RGBA"
    assert engine["precision"] == "injected"
