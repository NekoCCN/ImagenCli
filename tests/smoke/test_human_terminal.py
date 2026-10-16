"""Human output is Chinese, terminal-width safe and free of color codes."""

import subprocess
import sys
from pathlib import Path

import pytest

from imagen_cli.cli.human import render


def _result(tmp_path: Path) -> dict:
    return {
        "schema_version": 1, "ok": True, "status": "succeeded",
        "data": {
            "job": {"id": "job_" + "1" * 32, "state": "succeeded",
                    "remote_state": "succeeded", "local_save_state": "saved",
                    "cost": {"actual": None, "estimate": None, "currency": None}},
            "artifacts": [{"id": "art_1", "path": str(tmp_path / "中文 路径" / "image.png")}],
        },
    }


def test_human_output_is_chinese_and_shows_absolute_path(tmp_path):
    text = render(_result(tmp_path))
    assert "任务 job_" in text
    assert "费用：未知（不视为免费）" in text
    assert str(tmp_path / "中文 路径" / "image.png") in text


def test_narrow_terminal_keeps_paths_complete(tmp_path):
    text = render(_result(tmp_path), columns=20)
    path = str(tmp_path / "中文 路径" / "image.png")
    assert path in text
    for line in text.splitlines():
        if not line.startswith("文件："):
            assert len(line) <= 20


def test_errors_are_reported_without_raw_exceptions():
    text = render({"ok": False, "status": "validation_error",
                   "error": {"message": "命令未成功完成。"}})
    assert text.startswith("失败：")
    assert "Traceback" not in text


def test_cli_human_mode_has_no_color_escapes(tmp_path):
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(Path(__file__).parents[2] / "src"),
           "HOME": str(tmp_path), "NO_COLOR": "1", "TERM": "dumb"}
    script = (
        "import socket,sys; socket.socket=lambda *a,**k: (_ for _ in ()).throw(AssertionError());"
        "socket.getaddrinfo=socket.socket;"
        "from imagen_cli.cli.entrypoint import main; sys.exit(main())"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, "--workspace", str(tmp_path / "ws"),
         "--output", "human", "job", "get", "job_" + "1" * 32],
        input=b"", env=env, capture_output=True, timeout=20, check=False,
    )
    assert proc.stderr == b""
    assert b"\\x1b[" not in proc.stdout
    assert "失败" in proc.stdout.decode("utf-8")


def test_colored_terminal_uses_panels_and_tables(tmp_path):
    text = render(_result(tmp_path), color=True, columns=80)
    assert "\x1b[" in text
    assert "任务" in text and "产物" in text
    assert "费用：未知" in text


def test_empty_result_and_refusal_and_partial_are_rendered_truthfully():
    empty = render({"ok": True, "status": "succeeded", "data": {}})
    assert empty
    refusal = render({"ok": False, "status": "safety_refusal",
                      "error": {"message": "供应商拒绝。"}})
    assert refusal.startswith("失败：")
    partial = render({
        "ok": True, "status": "partial_success",
        "data": {"job": {"id": "job_2", "state": "failed", "remote_state": "succeeded",
                         "local_save_state": "partial",
                         "cost": {"actual": 0.02, "estimate": None, "currency": "USD"}},
                 "artifacts": []},
    })
    assert "partial" in partial
    assert "0.02 USD" in partial


def test_save_failed_is_distinguishable_from_unknown():
    text = render({
        "ok": True, "status": "save_failed",
        "data": {"job": {"id": "job_3", "state": "failed", "remote_state": "succeeded",
                         "local_save_state": "failed", "cost": {}}},
    })
    assert "save_failed".replace("save_failed", "failed") in text
    assert "远端 succeeded" in text


@pytest.mark.parametrize("columns", [40, 80])
def test_paths_are_complete_at_40_and_80_columns(columns, tmp_path):
    text = render(_result(tmp_path), columns=columns)
    assert str(tmp_path / "中文 路径" / "image.png") in text
