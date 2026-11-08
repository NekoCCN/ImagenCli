"""Matting: pinned manifest, refusal without consent, and a real alpha round trip."""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np
import pytest
from PIL import Image

from imagen_cli.application import matting, model_weights, superres


def _fake_runner(size: int = 1024):
    """Stand-in for a model: a soft vertical ramp, so tiling seams are visible."""
    def run(tile: Image.Image) -> np.ndarray:
        ramp = np.linspace(-2.0, 2.0, size, dtype=np.float32)
        return np.tile(ramp, (size, 1))

    return run


def test_manifest_is_licence_clean():
    for name, entry in matting.MANIFEST.items():
        assert entry["license"].split(" (")[0] in {"MIT", "Apache-2.0"}, name
        assert entry["sha256"] and len(entry["sha256"]) == 64, name
        assert entry["url"].startswith("https://"), name
        assert entry["source"].startswith("https://"), name


def test_noncommercial_weights_are_not_shipped():
    """A CC BY-NC export must never reach the default catalog."""
    for name, entry in matting.MANIFEST.items():
        assert "NC" not in entry["license"].replace("MIT", ""), name
        assert "NonCommercial" not in entry["license"], name


def test_missing_weights_refuse_without_accept(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="accept=true"):
        matting.ensure_weights("birefnet-lite")


def test_weights_are_pinned_and_digest_checked(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    payload = b"not the real weights"

    def download(url: str, target) -> None:
        target.write_bytes(payload)

    with pytest.raises(ValueError, match="pinned digest"):
        matting.ensure_weights("birefnet-lite", accept=True, download_fn=download)


def test_download_records_the_digest(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    payload = b"matting weights for a test"
    monkeypatch.setitem(matting.MANIFEST["birefnet-lite"], "sha256",
                        hashlib.sha256(payload).hexdigest())

    def download(url: str, target) -> None:
        target.write_bytes(payload)

    path = matting.ensure_weights("birefnet-lite", accept=True, download_fn=download)
    assert path.read_bytes() == payload
    assert model_weights.recorded_digest("birefnet-lite") == hashlib.sha256(payload).hexdigest()


def test_catalog_reports_missing_and_license(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    report = matting.catalog()
    row = report["models"][0]
    assert row["model"] == "birefnet-lite"
    assert row["license"] == "MIT"
    assert row["state"] == "missing"
    assert row["downloaded"] is False


def test_install_preview_downloads_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    report = matting.install(["birefnet-lite"], accept=False)
    assert report["models"][0]["downloaded"] is False
    assert not (tmp_path / "birefnet-lite.onnx").exists()
    assert "hint" in report


def test_matte_adds_alpha_and_keeps_rgb(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    source = Image.new("RGB", (64, 48), (10, 120, 200))
    result, engine = matting.matte(source, tile=64, overlap=0, runner=_fake_runner())
    assert result.mode == "RGBA"
    assert result.size == source.size
    assert result.convert("RGB").tobytes() == source.tobytes()
    alpha = result.getchannel("A")
    assert alpha.getextrema() != (255, 255)
    assert engine["tile"] == 64


def test_matting_does_not_touch_the_source():
    source = Image.new("RGB", (32, 32), (1, 2, 3))
    before = source.tobytes()
    matting.matte(source, tile=32, overlap=0, runner=_fake_runner())
    assert source.tobytes() == before
    assert source.mode == "RGB"


def test_tiled_run_covers_the_whole_image():
    source = Image.new("RGB", (200, 150), (0, 0, 0))
    result, engine = matting.matte(source, tile=64, overlap=16, runner=_fake_runner())
    assert engine["tile"] == 64
    assert result.size == (200, 150)
    # A zero weight anywhere would leave fully transparent holes after the divide.
    assert min(result.getchannel("A").getextrema()) == 0


def test_feather_and_edge_shift_are_bounded():
    source = Image.new("RGB", (32, 32))
    with pytest.raises(ValueError, match="feather"):
        matting.matte(source, tile=32, overlap=0, feather=99, runner=_fake_runner())
    with pytest.raises(ValueError, match="edge_shift"):
        matting.matte(source, tile=32, overlap=0, edge_shift=2.0, runner=_fake_runner())


def test_unknown_model_is_rejected():
    with pytest.raises(ValueError, match="unknown matting model"):
        matting.matte(Image.new("RGB", (8, 8)), model="nope", runner=_fake_runner())


def test_op_is_registered():
    from imagen_cli.application import media_ops

    assert media_ops.OPS["matte"] is matting.op


def test_matte_output_feeds_the_alpha_only_operators():
    from imagen_cli.application import media_ops

    result, _engine = matting.matte(
        Image.new("RGB", (32, 32), (200, 20, 20)), tile=32, overlap=0,
        runner=_fake_runner(),
    )
    outlined = media_ops.OPS["sticker_outline"](result, width=2)
    assert outlined.mode == "RGBA"
    blurred = media_ops.OPS["background_blur"](result, radius=2.0)
    assert blurred.mode == "RGBA"


def test_presets_chain_matting_first():
    from imagen_cli.application.operator_catalog import PRESET_PIPELINES

    for key in ("抠图贴纸 (Cutout Sticker)", "抠图虚化 (Cutout Bokeh)"):
        assert PRESET_PIPELINES[key][0]["op"] == "matte"
        assert PRESET_PIPELINES[key][0]["params"]["model"] in matting.MANIFEST


def test_model_ops_are_treated_as_model_steps():
    from imagen_cli.application import postprocess_worker

    assert postprocess_worker._has_model_steps([{"op": "matte", "params": {}}])
    assert not postprocess_worker._has_model_steps([{"op": "blur", "params": {}}])


def test_matting_is_not_reported_as_deterministic():
    from imagen_cli.application import postprocess_service

    assert "matte" in postprocess_service.NONDETERMINISTIC_OPS


def test_budget_accepts_matte_and_rejects_unknown_models():
    from imagen_cli.application import postprocess_service

    steps = postprocess_service.validate([{"op": "matte", "params": {}}])
    postprocess_service.check_budget((64, 64), steps)
    with pytest.raises(ValueError, match="unknown matting model"):
        postprocess_service.validate([{"op": "matte", "params": {"model": "nope"}}])


def test_superres_still_owns_its_own_manifest():
    """The shared weight store must not merge the two families."""
    assert "birefnet-lite" not in superres.MANIFEST
    assert "realesrgan-x4plus" not in matting.MANIFEST


def test_catalog_doctor_requires_a_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(matting, "available_providers", lambda: [])
    report = matting.doctor(backend="cpu")
    assert report["status"] == "missing"
    assert report["inference"] is False


def test_worker_reports_matte_capability_only_when_weights_exist(tmp_path, monkeypatch):
    from imagen_cli.remote import worker_cli

    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(matting, "available_providers", lambda: ["CPUExecutionProvider"])
    assert worker_cli._matting_ready() is False

    payload = b"weights"
    monkeypatch.setitem(matting.MANIFEST["birefnet-lite"], "sha256",
                        hashlib.sha256(payload).hexdigest())
    matting.weights_path("birefnet-lite").write_bytes(payload)
    assert worker_cli._matting_ready() is True


def test_worker_reports_matte_as_unsupported_without_a_runtime(tmp_path, monkeypatch):
    from imagen_cli.remote import worker_cli

    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(matting, "available_providers", lambda: [])
    report = worker_cli.handle({"schema_version": 1, "operation": "probe"})
    assert report["capabilities"]["matte"] is False


def test_worker_matte_runs_the_local_operator(tmp_path, monkeypatch):
    from imagen_cli.remote import worker_cli

    source = tmp_path / "in.png"
    Image.new("RGB", (24, 24), (200, 30, 30)).save(source)
    output = tmp_path / "out.png"

    calls = {}

    def fake_matte(image, **kwargs):
        calls.update(kwargs)
        result = image.convert("RGBA")
        result.putalpha(128)
        return result, {"provider": "CPUExecutionProvider"}

    monkeypatch.setattr(matting, "matte", fake_matte)
    response = worker_cli.handle({
        "schema_version": 1,
        "operation": "matte",
        "input": str(source),
        "output": str(output),
        "params": {"model": "birefnet-lite", "feather": 3},
    })
    assert response["status"] == "succeeded"
    assert calls["feather"] == 3
    assert calls["accept"] is False
    assert Image.open(output).mode == "RGBA"


def test_worker_matte_refuses_an_unknown_model(tmp_path, monkeypatch):
    from imagen_cli.remote import worker_cli

    source = tmp_path / "in.png"
    Image.new("RGB", (8, 8)).save(source)
    response = worker_cli.handle({
        "schema_version": 1,
        "operation": "matte",
        "input": str(source),
        "output": str(tmp_path / "out.png"),
        "params": {"model": "not-a-model"},
    })
    assert response["status"] == "failed"
    assert "unknown matting model" in response["error"]["message"]


def test_matting_doctor_names_a_provider_that_failed_to_load(tmp_path, monkeypatch):
    from imagen_cli.application import superres

    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(
        matting, "available_providers",
        lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    matting.weights_path("birefnet-lite").write_bytes(b"weights")
    monkeypatch.setitem(matting.MANIFEST["birefnet-lite"], "sha256", None)

    def fake_matte(*args, **kwargs):
        raise superres.ProviderUnavailable("the requested provider failed to load")

    monkeypatch.setattr(matting, "matte", fake_matte)
    data = matting.doctor("birefnet-lite", backend="cuda")
    assert data["status"] == "missing"
    assert data["inference"] is False
    assert data["missing"] == ["CUDAExecutionProvider"]


def test_matte_leaves_sharp_alpha_by_default_without_unconditional_blur():
    def step_runner(tile: Image.Image) -> np.ndarray:
        arr = np.zeros((1024, 1024), dtype=np.float32)
        arr[:, 512:] = 1.0
        return arr

    source = Image.new("RGB", (1024, 1024), (100, 100, 100))
    result, _ = matting.matte(
        source, tile=1024, overlap=0, feather=0, edge_shift=0.0, runner=step_runner,
    )
    alpha = np.asarray(result.getchannel("A"))
    assert alpha[512, 511] == 0
    assert alpha[512, 512] == 255



def test_make_runner_backend_reflects_actual_provider(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    class FakeSession:
        def get_providers(self):
            return ["CPUExecutionProvider"]

        def get_inputs(self):
            class Input:
                name = "input_image"
            return [Input()]

    fake_session = FakeSession()
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(
        get_available_providers=lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
        InferenceSession=lambda *args, **kwargs: fake_session,
    ))
    monkeypatch.setattr(
        matting, "available_providers",
        lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    monkeypatch.setattr(matting, "_preflight", lambda data: None)
    matting.weights_path("birefnet-lite").write_bytes(b"w")
    monkeypatch.setitem(matting.MANIFEST["birefnet-lite"], "sha256", None)

    _, engine = matting._make_runner(
        "birefnet-lite", backend=None, accept=True,
        session_factory=lambda *args, **kwargs: fake_session,
    )
    assert engine["provider"] == "CPUExecutionProvider"
    assert engine["backend"] == "cpu"



def test_matting_run_advertises_precision():
    from imagen_cli.cli.registry import discover

    schema = discover("matting run")["input_schema"]
    assert schema["properties"]["precision"]["enum"] == ["auto", "fp32", "fp16"]


def test_matting_run_carries_precision_into_the_transform(tmp_path, monkeypatch):
    from imagen_cli.application.container import Container
    from imagen_cli.cli.commands import postprocess

    captured: dict[str, Any] = {}

    def fake(repositories, store, artifact, **kwargs):
        captured.update(kwargs, artifact=artifact)
        return "succeeded", {}

    monkeypatch.setattr(postprocess, "transform_", fake)
    with Container(workspace=str(tmp_path / "ws")) as container:
        container.execute("matting run", {
            "artifact": "art_" + "a" * 32, "precision": "fp16",
        })
    assert captured["model_params"]["precision"] == "fp16"
    assert captured["op_name"] == "matte"


def test_matting_run_carries_precision_to_a_remote(tmp_path, monkeypatch):
    from imagen_cli.application import remote_service
    from imagen_cli.application.container import Container

    captured: dict[str, Any] = {}

    def fake(repositories, store, artifact, remote, operation, **kwargs):
        captured.update(kwargs, operation=operation)
        return "accepted", {"job": {"id": "job_1"}}

    monkeypatch.setattr(remote_service, "run_operation", fake)
    with Container(workspace=str(tmp_path / "ws")) as container:
        container.execute("matting run", {
            "artifact": "art_" + "a" * 32, "remote": "modal", "precision": "fp32",
        })
    assert captured["operation"] == "matte"
    assert captured["params"]["precision"] == "fp32"


def test_matting_cuda_without_a_provider_names_the_install_command(monkeypatch):
    from imagen_cli.application import matting

    monkeypatch.setattr(matting, "available_providers", lambda: ["CPUExecutionProvider"])
    monkeypatch.setattr(
        matting, "_choose_providers",
        lambda backend: ["CPUExecutionProvider"],
    )

    class Session:
        def get_providers(self):
            return ["CPUExecutionProvider"]

        def get_inputs(self):
            raise AssertionError("a refused provider must not be probed further")

    monkeypatch.setattr(matting, "ensure_weights", lambda model, accept=False: None)
    with pytest.raises(ValueError, match="install --backend cuda"):
        matting._require_provider(Session(), "cuda")


def test_the_matte_op_accepts_precision():
    from imagen_cli.application.postprocess_service import validate

    steps = validate([{"op": "matte", "params": {"model": "birefnet-lite",
                                                "precision": "fp16"}}])
    assert steps[0]["params"]["precision"] == "fp16"


def test_doctor_reports_a_broken_provider_instead_of_raising(monkeypatch):
    from imagen_cli.application import matting
    from imagen_cli.application.superres import ProviderUnavailable

    monkeypatch.setattr(
        matting, "available_providers",
        lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
    )

    def fail(*args, **kwargs):
        raise ProviderUnavailable(
            "backend cuda was requested but the session is using CPUExecutionProvider"
        )

    monkeypatch.setattr(matting, "_make_runner", fail)
    report = matting.doctor(backend="cuda")
    assert report["status"] == "missing"
    assert "install --backend cuda" in report["hint"]


def test_doctor_turns_an_allocation_failure_into_a_fp16_hint(monkeypatch):
    from imagen_cli.application import matting

    monkeypatch.setattr(
        matting, "available_providers",
        lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
    )

    def fail(*args, **kwargs):
        raise ValueError("backend cuda ran out of memory while running the model")

    monkeypatch.setattr(matting, "_make_runner", fail)
    report = matting.doctor(backend="cuda")
    assert report["status"] == "missing"
    assert "precision fp16" in report["hint"]


def test_auto_retries_when_the_session_cannot_allocate(monkeypatch, tmp_path):
    from imagen_cli.application import matting, superres

    weights = tmp_path / "stub.onnx"
    weights.write_bytes(b"stub")
    import sys
    import types

    fake_runtime = types.ModuleType("onnxruntime")
    fake_runtime.InferenceSession = object  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "onnxruntime", fake_runtime)
    monkeypatch.setattr(matting, "ensure_weights", lambda model, accept=False: weights)
    monkeypatch.setattr(matting, "_preflight", lambda data: None)
    monkeypatch.setattr(matting, "_choose_providers", lambda backend: ["CPUExecutionProvider"])
    monkeypatch.setitem(matting.MANIFEST, "stub", {
        "sha256": None, "input_size": 8, "mean": (0, 0, 0), "std": (1, 1, 1),
    })
    monkeypatch.setattr(matting, "_require_provider", lambda session, backend: None)
    from imagen_cli.application import matting_fp16

    monkeypatch.setattr(matting_fp16, "ensure_fp16", lambda model: (weights, {"derived": False}))
    opened = []

    def failing_session(factory, data, *, providers, backend):
        opened.append(backend)
        raise ValueError(
            f"backend {backend or 'auto'} ran out of memory while loading the model"
        )

    monkeypatch.setattr(superres, "_open_session", failing_session)
    with pytest.raises(ValueError, match="out of memory"):
        matting.matte(Image.new("RGB", (8, 8)), model="stub", backend="cuda")
    assert len(opened) == 2


def test_matting_run_parses_precision_from_argv():
    from imagen_cli.cli.entrypoint import _parse

    command, raw, _ = _parse([
        "matting", "run", "art_" + "a" * 32, "--precision", "fp16",
    ])
    assert command == "matting run"
    assert isinstance(raw, dict)
    assert raw["precision"] == "fp16"
