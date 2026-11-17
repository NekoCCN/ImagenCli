"""Cross-platform process identity dispatch without touching other platforms."""

import subprocess
import sys
from pathlib import Path

import pytest

from imagen_cli.application import cancel_service


def test_invalid_pids_are_refused():
    for value in (0, -1, "1", None):
        assert cancel_service.process_start(value) is None  # type: ignore[arg-type]


def test_linux_start_is_available_for_the_current_process():
    if not sys.platform.startswith("linux"):
        return
    assert cancel_service.process_start(99999999) is None
    assert cancel_service.process_start(__import__("os").getpid())


@pytest.mark.parametrize("state", ["Z", "X", "x", "S", "R"])
def test_linux_dead_process_is_not_an_executor(monkeypatch, state):
    # The command name may itself contain spaces and parentheses.
    stat = "123 (worker (child)) " + " ".join([state] + ["0"] * 18 + ["456"])
    monkeypatch.setattr(Path, "read_text", lambda self: stat)
    assert cancel_service._linux_start(123) == (None if state in {"Z", "X", "x"} else "456")


def test_darwin_start_parses_ps_output(monkeypatch):
    calls = []

    def fake_run(arguments, **kwargs):
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "Sat Oct 24 10:00:00 2026\n", "")

    monkeypatch.setattr(cancel_service.sys, "platform", "darwin")
    monkeypatch.setattr(cancel_service.subprocess, "run", fake_run)
    assert cancel_service.process_start(321) == "Sat Oct 24 10:00:00 2026"
    assert calls == [["ps", "-p", "321", "-o", "lstart="]]


def test_darwin_start_returns_none_for_missing_process(monkeypatch):
    monkeypatch.setattr(cancel_service.sys, "platform", "darwin")
    monkeypatch.setattr(
        cancel_service.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, "", "no process"),
    )
    assert cancel_service.process_start(321) is None


def test_unknown_platform_is_unsupported(monkeypatch):
    monkeypatch.setattr(cancel_service.sys, "platform", "freebsd13")
    assert cancel_service.process_start(1) is None
