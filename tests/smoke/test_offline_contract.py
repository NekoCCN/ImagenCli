import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def invoke(args):
    env = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(ROOT / "src"),
        "HOME": "/nonexistent",
        "NO_COLOR": "1",
    }
    # Install a guard before importing the entrypoint: even DNS and sockets are forbidden.
    script = (
        "import socket,sys; "
        "socket.socket=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network')); "
        "socket.getaddrinfo=socket.socket; "
        "from imagen_cli.cli.entrypoint import main; sys.exit(main())"
    )
    return subprocess.run(
        [sys.executable, "-c", script, *args], input=b"", env=env, capture_output=True, timeout=5
    )


@pytest.mark.parametrize(
    "args",
    [
        ["--help"],
        ["help", "--agent"],
        ["schema"],
        ["schema", "generate"],
        ["schema", "--output", "jsonl"],
    ],
)
def test_help_and_schema_offline_with_closed_stdin(args):
    proc = invoke(args)
    assert proc.returncode == 0, proc.stderr.decode()
    records = [json.loads(line) for line in proc.stdout.splitlines()]
    result = records[-1].get("result", records[-1])
    assert result["ok"] is True
    assert not proc.stderr


@pytest.mark.parametrize(
    "args,mode",
    [
        (["--output", "json", "--output", "jsonl"], "json"),
        (["--output", "jsonl", "--output", "json"], "jsonl"),
        (["--output=jsonl", "--unknown=secret-value"], "jsonl"),
        (["--unknown=secret-value"], "json"),
    ],
)
def test_errors_keep_first_output_mode_and_never_echo_values(args, mode):
    proc = invoke(args)
    assert proc.returncode == 2
    assert b"secret-value" not in proc.stdout + proc.stderr
    records = [json.loads(line) for line in proc.stdout.splitlines()]
    result = records[-1]["result"] if mode == "jsonl" else records[0]
    assert result["status"] == "validation_error"
    assert "Traceback" not in proc.stderr.decode()


def test_undelivered_operation_is_explicit():
    proc = invoke(["generate"])
    assert proc.returncode == 2
    assert json.loads(proc.stdout)["status"] == "unsupported_capability"


def test_internal_exception_is_not_rendered(monkeypatch):
    import io

    from imagen_cli.application.container import Container
    from imagen_cli.cli.entrypoint import main

    def fail(*args, **kwargs):
        raise RuntimeError("api_key=secret-value prompt=private")

    monkeypatch.setattr(Container, "execute", fail)
    out, err = io.StringIO(), io.StringIO()
    assert main(["schema", "--output", "json"], stdout=out, stderr=err) == 1
    assert "secret-value" not in out.getvalue() + err.getvalue()
    assert json.loads(out.getvalue())["status"] == "internal_error"
