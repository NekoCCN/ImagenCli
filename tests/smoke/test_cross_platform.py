"""Portable paths, UTF-8 output and narrow terminals behave the same offline."""

import json
import os
import subprocess
import sys
from pathlib import Path

from imagen_cli.cli.human import render


def test_chinese_workspace_paths_round_trip(tmp_path):
    from tests.integration.test_local_cli import invoke

    workspace = tmp_path / "工作 目录"
    code, result = invoke(workspace, "session", "create", "海报")
    assert code == 0
    assert result["data"]["session"]["name"] == "海报"


def test_narrow_utf8_human_output_has_no_escape_codes(tmp_path):
    value = {
        "ok": True, "status": "succeeded",
        "data": {"job": {"id": "job_1", "state": "succeeded", "remote_state": "succeeded",
                         "local_save_state": "saved", "cost": {}},
                 "artifacts": [{"path": str(tmp_path / "图片" / "结果.png")}]},
    }
    text = render(value, columns=30)
    assert "文件：" in text
    assert "\x1b[" not in text
    assert str(tmp_path / "图片" / "结果.png") in text
    assert all(
        len(line) <= 30 for line in text.splitlines() if not line.startswith("文件：")
    )


def test_cli_json_is_ascii_safe_and_parseable_with_no_color(tmp_path):
    env = {"PATH": os.environ["PATH"],
           "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
           "HOME": str(tmp_path), "NO_COLOR": "1", "LANG": "C"}
    script = (
        "import socket,sys; socket.socket=lambda *a,**k: (_ for _ in ()).throw(AssertionError());"
        "socket.getaddrinfo=socket.socket;"
        "from imagen_cli.cli.entrypoint import main; sys.exit(main())"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, "--workspace", str(tmp_path / "ws"),
         "--output", "json", "session", "create", "中文会话"],
        input=b"", env=env, capture_output=True, timeout=20, check=False,
    )
    assert proc.returncode == 0, proc.stdout
    value = json.loads(proc.stdout)
    assert value["data"]["session"]["name"] == "中文会话"
