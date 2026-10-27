"""Runtime installs are explicit and target only the running virtual environment."""

import subprocess

import pytest

from imagen_cli.application import runtime_service as runtime


def environment(monkeypatch):
    monkeypatch.setattr(runtime.sys, "prefix", "/tmp/imagen-env")
    monkeypatch.setattr(runtime.sys, "base_prefix", "/usr")
    monkeypatch.setattr(runtime.sys, "executable", "/tmp/imagen-env/bin/python")
    monkeypatch.setattr(runtime.shutil, "which", lambda name: "/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(runtime, "version", lambda name: "1.0" if name == "numpy" else None)


def test_preview_does_not_install(monkeypatch):
    environment(monkeypatch)
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **kw: pytest.fail("network"))
    status, data = runtime.install()
    assert status == "succeeded" and data["installed"] is False
    assert data["command"] == [
        "/bin/uv",
        "pip",
        "install",
        "--python",
        "/tmp/imagen-env/bin/python",
        "onnxruntime>=1.20",
        "onnx>=1.20",
        "numpy>=2",
    ]


def test_install_targets_running_interpreter(monkeypatch):
    environment(monkeypatch)

    def run(command, **kwargs):
        assert command[:5] == [
            "/bin/uv",
            "pip",
            "install",
            "--python",
            "/tmp/imagen-env/bin/python",
        ]
        assert kwargs["timeout"] == 900 and kwargs["check"] is False
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(runtime.subprocess, "run", run)
    assert runtime.install(accept=True)[1]["installed"] is True


def test_system_python_is_not_mutated(monkeypatch):
    environment(monkeypatch)
    monkeypatch.setattr(runtime.sys, "prefix", "/usr")
    with pytest.raises(ValueError, match="virtual environment"):
        runtime.install(accept=True)


@pytest.mark.parametrize("other", ["onnxruntime-gpu", "onnxruntime-directml"])
def test_conflicting_backend_is_not_uninstalled(monkeypatch, other):
    environment(monkeypatch)
    monkeypatch.setattr(runtime, "version", lambda name: "1.0" if name == other else None)
    with pytest.raises(ValueError, match="conflict"):
        runtime.install(accept=True)


def test_invalid_backend_rejected(monkeypatch):
    environment(monkeypatch)
    with pytest.raises(ValueError, match="backend"):
        runtime.install(backend="shell injection")


@pytest.mark.parametrize("failure", ["exit", "timeout"])
def test_installer_failure_is_not_success(monkeypatch, failure):
    environment(monkeypatch)

    def run(command, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 900)
        return subprocess.CompletedProcess(command, 1, "", "installer failed")

    monkeypatch.setattr(runtime.subprocess, "run", run)
    with pytest.raises(ValueError, match="failed|timed out"):
        runtime.install(accept=True)
