"""ACG/cel restoration: deterministic cleanup ops and optional model install."""

import hashlib
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from imagen_cli.application import media_ops, superres
from imagen_cli.application.operator_catalog import (
    PRESET_CATEGORIES,
    PRESET_PIPELINES,
    get_intro_text,
    get_operator_item,
)


def _noisy_cel_image() -> Image.Image:
    image = Image.new("RGBA", (16, 12), (120, 160, 200, 180))
    pixels = image.load()
    assert pixels is not None
    for y in range(image.height):
        for x in range(image.width):
            drift = 30 if (x + y) % 2 else -30
            pixels[x, y] = (120 + drift, 160, 200 - drift, 180)
    return image


def test_chroma_denoise_reduces_color_noise_and_keeps_alpha():
    source = _noisy_cel_image()
    output = media_ops.chroma_denoise(source, radius=2, strength=1.0)
    assert output.mode == "RGBA"
    assert output.size == source.size
    assert output.getchannel("A").tobytes() == source.getchannel("A").tobytes()

    def chroma_spread(image: Image.Image) -> int:
        blue = image.convert("YCbCr").tobytes()[2::3]
        return max(blue) - min(blue)

    assert chroma_spread(output) < chroma_spread(source)


def test_chroma_denoise_validates_parameters():
    source = Image.new("RGB", (8, 8))
    with pytest.raises(ValueError, match="radius"):
        media_ops.chroma_denoise(source, radius=0)
    with pytest.raises(ValueError, match="strength"):
        media_ops.chroma_denoise(source, strength=1.5)


def test_edge_antialias_keeps_size_and_alpha():
    source = Image.new("RGBA", (24, 24), (0, 0, 0, 255))
    for x in range(4, 20):
        for y in range(4, 20):
            source.putpixel((x, y), (240, 240, 240, 255))
    output = media_ops.edge_antialias(source, radius=1.0, strength=0.6)
    assert output.size == source.size
    assert output.getchannel("A").tobytes() == source.getchannel("A").tobytes()


def test_cel_banding_reduce_is_deterministic_and_preserves_size():
    gradient = Image.new("RGB", (64, 8))
    for x in range(64):
        value = 40 + (x // 8) * 8
        for y in range(8):
            gradient.putpixel((x, y), (value, value, value))
    first = media_ops.cel_banding_reduce(gradient, strength=0.5)
    second = media_ops.cel_banding_reduce(gradient, strength=0.5)
    assert first.size == gradient.size
    assert first.tobytes() == second.tobytes()
    values = first.convert("L").tobytes()
    assert 35 <= sum(values) / len(values) <= 125


@pytest.mark.parametrize(
    "preset",
    [
        "ACG 赛璐璐轻修复 (ACG Cel Cleanup)",
        "ACG 压缩截图修复 (ACG Compressed Still)",
    ],
)
def test_acg_presets_use_registered_operators(preset):
    assert preset in PRESET_PIPELINES
    assert preset in PRESET_CATEGORIES
    for step in PRESET_PIPELINES[preset]:
        assert step["op"] in media_ops.OPS
    assert get_intro_text(preset)


def test_acg_operators_are_browsable_with_defaults():
    for op in ("chroma_denoise", "edge_antialias", "cel_banding_reduce"):
        item = get_operator_item(op)
        assert item is not None
        assert item.category == "acg"
        defaults = {parameter.name: parameter.default for parameter in item.parameters}
        assert defaults == item.default_params
        assert item.build_pipeline() == [{"op": op, "params": defaults}]


def test_anime_video_model_is_pinned_and_documented():
    entry = superres.MANIFEST["realesr-animevideov3"]
    assert entry["scale"] == 4
    assert entry["license"] == "BSD-3-Clause"
    assert entry["sha256"] == "78baa685a1a92cac6e14ab2af2a8ad0ef56fa48629563b1414e3a7acc54d86e2"
    assert entry["category"] == "acg"
    row = next(
        item for item in superres.catalog()["models"]
        if item["model"] == "realesr-animevideov3"
    )
    assert row["downloadable"] is True
    assert row["task"] == "upscale"


def test_install_preview_does_not_touch_runtime_or_network(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(superres, "available_providers", lambda: [])
    calls = []
    data = superres.install(
        ["realesr-animevideov3"],
        accept=False,
        backend="cpu",
        runtime_install=lambda **kwargs: calls.append(("runtime", kwargs)),
        download=lambda *args: calls.append(("download", args)),
    )
    assert calls == []
    assert data["installed"] is False
    assert data["runtime_installed"] is False
    row = data["models"][0]
    assert row["model"] == "realesr-animevideov3"
    assert row["downloaded"] is False
    assert row["license"] == "BSD-3-Clause"
    assert "accept" in data["hint"]


def test_install_accept_runs_runtime_then_downloads(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    digest = hashlib.sha256(b"weights").hexdigest()
    monkeypatch.setitem(superres.MANIFEST, "test-model", {
        "url": "https://example.invalid/test.onnx",
        "sha256": digest,
        "scale": 1,
        "license": "test",
        "source": "https://example.invalid",
    })
    monkeypatch.setattr(superres, "available_providers", lambda: [])
    calls = []

    def runtime_install(**kwargs):
        calls.append(("runtime", kwargs))
        return "succeeded", {"installed": True}

    def download(url: str, target: Path) -> None:
        calls.append(("download", url))
        target.write_bytes(b"weights")

    data = superres.install(
        ["test-model"],
        accept=True,
        backend="cpu",
        runtime_install=runtime_install,
        download=download,
    )
    assert [name for name, _ in calls] == ["runtime", "download"]
    assert calls[0][1]["accept"] is True
    assert data["installed"] is True
    row = data["models"][0]
    assert row["downloaded"] is True
    assert row["bytes"] == len(b"weights")
    assert (tmp_path / "test-model.onnx").read_bytes() == b"weights"
    assert (tmp_path / "test-model.sha256").read_text(encoding="ascii") == digest


def test_install_rejects_unknown_model(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="unknown super-resolution model"):
        superres.install(["not-a-model"], accept=False)


def test_install_without_a_model_touches_only_the_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(superres, "available_providers", lambda: [])
    calls: list[dict[str, Any]] = []

    def runtime_install(**kwargs: Any) -> tuple[str, dict[str, Any]]:
        calls.append(kwargs)
        return "succeeded", {"installed": True}

    preview = superres.install(
        None, accept=False, runtime_install=runtime_install, download=lambda *args: calls.append({})
    )
    assert calls == []
    assert preview["models"] == []
    assert preview["installed"] is False
    assert "runtime_installed" in preview

    data = superres.install(
        None, accept=True, backend="cpu", runtime_install=runtime_install
    )
    assert calls == [{"backend": "cpu", "accept": True}]
    assert data["models"] == []
    assert data["runtime_installed"] is True
    assert data["installed"] is True
