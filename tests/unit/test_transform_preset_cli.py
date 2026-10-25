"""Tests for CLI transform --preset and ops commands with full intro catalog."""

import io
import json
from pathlib import Path

from PIL import Image

from imagen_cli.application.container import Container
from imagen_cli.cli.entrypoint import main


def _create_ws(tmp_path: Path) -> tuple[Path, str]:
    ws = tmp_path / "ws"
    ws.mkdir()
    art_id = "art_" + "7" * 32
    sess_id = "ses_" + "7" * 32
    img = Image.new("RGB", (32, 32), (180, 100, 100))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw = buf.getvalue()

    with Container(workspace=str(ws)) as c:
        c.repositories.insert("sessions", {
            "id": sess_id, "name": "test_s", "version": 0, "created_at": "2026-10-24T00:00:00Z"
        })
        c.artifact_store.stage(art_id, raw)
        c.artifact_store.publish(art_id)
        c.repositories.insert("artifact_links", {
            "session_id": sess_id, "artifact_id": art_id, "role": "output",
            "created_at": "2026-10-24T00:00:00Z",
        })
    return ws, art_id


def test_cli_transform_with_preset(tmp_path: Path):
    ws, art_id = _create_ws(tmp_path)
    stdout = io.StringIO()
    code = main([
        "--workspace", str(ws),
        "transform", art_id,
        "--preset", "日系治愈空气柔光",
        "--output", "json",
    ], stdout=stdout)
    assert code == 0
    resp = json.loads(stdout.getvalue())
    assert resp["ok"] is True
    assert "artifact" in resp["data"]
    assert resp["data"]["artifact"]["id"] != art_id


def test_cli_ops_lists_operations_and_presets(tmp_path: Path):
    ws = tmp_path / "ws_ops"
    ws.mkdir()
    stdout = io.StringIO()
    code = main([
        "--workspace", str(ws),
        "ops",
        "--output", "json",
    ], stdout=stdout)
    assert code == 0
    resp = json.loads(stdout.getvalue())
    assert resp["ok"] is True
    ops = resp["data"]["operations"]
    presets = resp["data"]["presets"]
    assert "diffuse_glow" in ops
    assert "line_color" in ops
    assert any("日系治愈" in p for p in presets)


def test_cli_ops_specific_op_details(tmp_path: Path):
    ws = tmp_path / "ws_details"
    ws.mkdir()
    stdout = io.StringIO()
    code = main([
        "--workspace", str(ws),
        "ops", "--op", "diffuse_glow",
        "--output", "json",
    ], stdout=stdout)
    assert code == 0
    resp = json.loads(stdout.getvalue())
    assert resp["ok"] is True
    assert "details" in resp["data"]
    detail = resp["data"]["details"]["diffuse_glow"]
    assert "高光模糊" in detail["title"]
    assert detail["principle"]
    assert detail["benefit"]
    assert detail["loss"]
    assert detail["use_case"]


def test_cli_transform_with_anime_presets(tmp_path: Path):
    ws, art_id = _create_ws(tmp_path)
    stdout = io.StringIO()
    code = main([
        "--workspace", str(ws),
        "transform", art_id,
        "--preset", "二次元线条强化与描墨",
        "--output", "json",
    ], stdout=stdout)
    assert code == 0
    resp = json.loads(stdout.getvalue())
    assert resp["ok"] is True
    assert "artifact" in resp["data"]


def test_operator_catalog_basic_and_advanced_parameters():
    from imagen_cli.application.operator_catalog import get_operator_item

    item = get_operator_item("anime_line_enhance")
    assert item is not None
    basic_names = [p.name for p in item.basic_parameters]
    adv_names = [p.name for p in item.advanced_parameters]

    # 基础参数浓缩为最关键的 2 个宏观滑块
    assert "strength" in basic_names
    assert "thickness" in basic_names
    assert len(basic_names) == 2

    # 高级参数包含全部 7 个微观底层参数
    assert len(adv_names) == 7
    assert "sigma" in adv_names
    assert "k_ratio" in adv_names
    assert "closing_radius" in adv_names

    # 验证表面平滑算子的基础/高级划分
    surf = get_operator_item("surface_blur")
    assert surf is not None
    assert [p.name for p in surf.basic_parameters] == ["blend"]
    assert len(surf.advanced_parameters) == 3


def test_cli_ops_anti_ai_artifacts_suppression_preset(tmp_path: Path):
    ws = tmp_path / "ws_anti_ai_suppression"
    ws.mkdir()
    stdout = io.StringIO()
    code = main([
        "--workspace", str(ws),
        "ops", "--preset", "抗AI生成伪影",
        "--output", "json",
    ], stdout=stdout)
    assert code == 0
    resp = json.loads(stdout.getvalue())
    assert resp["ok"] is True
    assert "details" in resp["data"]
    matched = next(iter(resp["data"]["details"].values()))
    assert "pipeline" in matched
    ops_in_pipeline = [step["op"] for step in matched["pipeline"]]
    assert "anti_checkerboard" in ops_in_pipeline
    assert "subsurface_scattering" in ops_in_pipeline
    assert "de_haloing" in ops_in_pipeline

