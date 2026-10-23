"""Detached worker consumes a queued async job through a local provider stub."""

from __future__ import annotations

import base64
import json
import os
import signal
import threading
import time
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from imagen_cli.execution import worker as worker_module
from tests.integration.test_local_cli import invoke


def _handler(png_bytes: bytes) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - http.server API
            length = int(self.headers.get("content-length", "0"))
            self.rfile.read(length)
            body = json.dumps({
                "data": [{"b64_json": base64.b64encode(png_bytes).decode()}],
            }).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: object) -> None:
            return

    return Handler


@pytest.mark.skipif(os.name == "nt", reason="detached POSIX worker test")
def test_worker_executes_a_queued_async_job(tmp_path: Path, png_bytes: bytes) -> None:
    if not worker_module.detached_supported():
        pytest.skip("detached worker is unsupported on this platform")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(png_bytes))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    workspace = tmp_path / "work"
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("red kite", encoding="utf-8")
    key = tmp_path / "key.txt"
    key.write_text("local-test-key", encoding="utf-8")
    pid: int | None = None
    try:
        assert invoke(workspace, "profile", "set", "async", "--provider", "openai",
                      "--family", "images", "--base-url", base_url,
                      "--model-default", "image-model",
                      "--credential-ref", "env:UNUSED", "--revision", "1")[0] == 0
        assert invoke(workspace, "profile", "secret", "async",
                      "--key-file", str(key))[0] == 0
        assert invoke(workspace, "session", "create", "poster",
                      "--profile", "async")[0] == 0
        code, queued = invoke(workspace, "generate", "--async", "--session", "poster",
                              "--profile", "async", "--prompt-file", str(prompt))
        assert code == 0 and queued["status"] == "accepted", queued
        job = queued["data"]["job"]["id"]
        assert queued["data"]["job"]["worker_queued"] is True

        code, started = invoke(workspace, "worker", "start")
        assert code == 0 and started["data"]["worker"]["state"] == "running", started
        pid = started["data"]["worker"]["pid"]

        code, finished = invoke(workspace, "job", "wait", job, "--timeout", "30")
        assert code == 0, finished
        assert finished["status"] == "succeeded", finished
        artifacts = finished["data"]["artifacts"]
        assert len(artifacts) == 1
        assert Path(artifacts[0]["path"]).is_file()

        code, status = invoke(workspace, "worker", "status")
        assert code == 0
        assert status["data"]["workers"][0]["alive"] is True
    finally:
        if pid is not None:
            with suppress(ProcessLookupError):
                os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                with suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
        server.shutdown()
        server.server_close()
        thread.join(5)

