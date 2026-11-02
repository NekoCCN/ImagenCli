"""Readiness is evaluated for the requested backend, never for any runtime."""

import subprocess
import sys

from imagen_cli.application import superres


def test_cuda_install_runs_when_only_the_cpu_provider_exists(monkeypatch, tmp_path):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(superres, "available_providers", lambda: ["CPUExecutionProvider"])
    calls = []

    def runtime_install(**kwargs):
        calls.append(kwargs)
        return "succeeded", {"installed": True}

    data = superres.install(
        None,
        accept=True,
        backend="cuda",
        runtime_install=runtime_install,
        provider_probe=lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    assert calls == [{"backend": "cuda", "accept": True}]
    assert data["runtime_installed"] is True


def test_preview_reports_the_requested_backend_not_any_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(superres, "available_providers", lambda: ["CPUExecutionProvider"])
    data = superres.install(None, accept=False, backend="cuda")
    assert data["runtime_installed"] is False


def test_a_probe_without_the_requested_provider_is_not_success(monkeypatch, tmp_path):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(superres, "available_providers", lambda: [])
    data = superres.install(
        None,
        accept=True,
        backend="cuda",
        runtime_install=lambda **kwargs: ("succeeded", {"installed": True}),
        provider_probe=lambda: ["CPUExecutionProvider"],
    )
    assert data["runtime_installed"] is False
    assert data["installed"] is False


def test_probe_runs_in_a_fresh_isolated_interpreter(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "CUDAExecutionProvider\n", "")

    monkeypatch.setattr(subprocess, "run", run)
    assert superres._subprocess_providers() == ["CUDAExecutionProvider"]
    assert calls[0][0][0] == sys.executable
    assert calls[0][0][1] == "-I"
