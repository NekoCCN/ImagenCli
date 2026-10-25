"""Behavior checks for the reproducible offline smoothing evaluation."""

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
from PIL import Image  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "eval_postprocess.py"


def load_harness():
    spec = importlib.util.spec_from_file_location("eval_postprocess", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_texture_metric_detects_interior_one_pixel_checker():
    harness = load_harness()
    values = (np.indices((8, 8)).sum(axis=0) % 2 * 60 + 100).astype("uint8")
    source = Image.fromarray(values)
    assert harness.mean_neighbor_difference(source) == 60
    assert harness.mean_neighbor_difference(Image.new("L", (8, 8), 130)) == 0


def test_cli_writes_reproducible_cases_without_overwriting(tmp_path):
    output = tmp_path / "evaluation"
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(output)],
        capture_output=True, text=True, timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    report_path = output / "report.json"
    report = json.loads(report_path.read_text())
    assert {case["case"] for case in report["cases"]} == {
        "line_1px", "cel_edge", "checker_1px",
    }
    assert report["metric"] == "pillow_l_mean_abs_neighbor_difference_v1"
    assert report["environment"]["numpy"] == np.__version__
    assert report["variants"]["local_laplacian_8"]["params"]["levels"] == 5
    assert report["paired_scope"] == "synthetic additive RGB Gaussian noise only"
    assert {case["case"] for case in report["paired_cases"]} == {
        "cel_edge_gaussian", "line_1px_gaussian",
    }
    for case in report["paired_cases"]:
        assert case["degradation"] == {
            "kind": "additive_rgb_gaussian", "seed": 20261025, "sigma": 8,
            "encoding": "srgb_u8_clip_round",
        }
        assert case["clean_sha256"] == hashlib.sha256(
            (output / case["clean_file"]).read_bytes(),
        ).hexdigest()
        assert case["input_sha256"] == hashlib.sha256(
            (output / case["input_file"]).read_bytes(),
        ).hexdigest()
        with Image.open(output / case["clean_file"]) as clean_image:
            clean = np.asarray(clean_image, dtype=np.float64)
        with Image.open(output / case["input_file"]) as noisy_image:
            noisy = np.asarray(noisy_image, dtype=np.float64)
        assert 0 < case["input_mae_vs_clean"] < 10
        assert 0 < case["input_ssim_vs_clean"] < 1
        assert case["input_mae_vs_clean"] == pytest.approx(np.abs(noisy - clean).mean())
        assert len(case["variants"]) == 4
        for variant in case["variants"]:
            with Image.open(output / variant["file"]) as result:
                restored = np.asarray(result, dtype=np.float64)
                assert variant["ssim_vs_clean"] == pytest.approx(
                    load_harness().ssim(Image.fromarray(clean.astype("uint8")), result),
                )
            assert variant["mae_vs_clean"] == pytest.approx(np.abs(restored - clean).mean())
    assert set(report["transparency"]) == {"binary", "fractional"}
    for checks in report["transparency"].values():
        assert len(checks) == 4
        for check in checks.values():
            assert check["alpha_exact"]
            assert check["hidden_preserved"]
            assert check["visible_ignores_hidden"]
    original = report_path.read_bytes()
    repeated = subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(output)],
        capture_output=True, text=True, timeout=60,
    )
    assert repeated.returncode != 0
    assert report_path.read_bytes() == original


def test_explicit_local_input_keeps_native_size_and_records_matching_crops(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (300, 270), (80, 120, 180)).save(source)
    original = source.read_bytes()
    output = tmp_path / "native"
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(source), "--output", str(output)],
        capture_output=True, text=True, timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads((output / "report.json").read_text())
    case = report["local_images"][0]
    assert case["source_size"] == [300, 270]
    assert case["color"]["output_profile"] == "srgb"
    assert len(case["variants"]) == 4
    for variant in case["variants"]:
        with Image.open(output / variant["file"]) as result:
            assert result.size == (300, 270)
            assert result.info["icc_profile"]
        assert variant["mae"] == 0
        assert variant["ssim_vs_source"] == pytest.approx(1)
        assert variant["crops"][0]["box"] == [22, 7, 278, 263]
        with Image.open(output / variant["crops"][0]["file"]) as crop:
            assert crop.size == (256, 256)
    assert source.read_bytes() == original


def test_ablation_records_one_parameter_changes(tmp_path):
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--ablation", "--output", str(tmp_path / "ablation")],
        capture_output=True, text=True, timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads((tmp_path / "ablation" / "report.json").read_text())
    variants = report["variants"]
    assert {v["params"]["radius"] for v in variants.values()
            if v["op"] == "weighted_guided_smooth"} == {4, 8, 16}
    assert {v["params"]["detail"] for v in variants.values()
            if v["op"] == "local_laplacian_sampled"} == {0.5, 1, 2}
    for case in report["cases"]:
        assert {v["label"] for v in case["variants"]} == set(variants)
