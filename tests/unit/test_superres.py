"""Super-resolution plumbing: tiling, providers and weight policy without downloads."""

import pytest

pytest.importorskip("numpy")

import numpy as np
from PIL import Image

from imagen_cli.application import superres


def nearest_runner(tile):
    return np.repeat(np.repeat(tile, 2, axis=0), 2, axis=1)


def test_tiled_upscale_matches_a_single_tile():
    array = np.arange(4 * 4 * 3, dtype=np.uint8).reshape(4, 4, 3)
    result = superres.tiled_upscale(array, nearest_runner, scale=2)
    expected = np.repeat(np.repeat(array, 2, axis=0), 2, axis=1)
    assert np.array_equal(result, expected)


def test_overlapping_tiles_are_averaged():
    array = np.full((16, 16, 3), 10, dtype=np.uint8)

    def noisy(tile):
        return np.repeat(np.repeat(tile, 2, axis=0), 2, axis=1) + tile.mean() / 100

    result = superres.tiled_upscale(array, noisy, scale=2, tile=8, overlap=2)
    assert result.shape == (32, 32, 3)
    assert np.allclose(result, 10.1, atol=0.01)


def test_unknown_model_and_backend_are_refused():
    image = Image.new("RGB", (4, 4))
    with pytest.raises(ValueError, match="unknown super-resolution model"):
        superres.upscale(image, model="nope", runner=nearest_runner)
    with pytest.raises(ValueError, match="ai-onnx|unavailable"):
        superres.upscale(image, model="realesrgan-x4plus", backend="cuda")
    with pytest.raises(ValueError, match="ai-torch"):
        superres.upscale(image, model="realesrgan-x4plus", backend="torch")


def test_models_without_an_upstream_export_require_a_local_file(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="place realesrgan-x4plus-anime.onnx"):
        superres.ensure_weights("realesrgan-x4plus-anime")
    path = tmp_path / "realesrgan-x4plus-anime.onnx"
    path.write_bytes(b"weights")
    assert superres.ensure_weights("realesrgan-x4plus-anime") == path


def test_pinned_models_require_accept_before_download(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="accept=true"):
        superres.ensure_weights("realesrgan-x4plus")
    with pytest.raises(ValueError, match="pinned digest"):
        superres.ensure_weights(
            "realesrgan-x4plus", accept=True,
            download=lambda url, target: target.write_bytes(b"wrong"),
        )


def test_manual_weights_record_and_enforce_a_digest(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    path = tmp_path / "realesrgan-x4plus-anime.onnx"
    path.write_bytes(b"weights")
    superres.ensure_weights("realesrgan-x4plus-anime")
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="digest mismatch"):
        superres.ensure_weights("realesrgan-x4plus-anime")


def test_alpha_is_carried_through_upscaling():
    image = Image.new("RGBA", (4, 4), (30, 60, 90, 128))
    quadruple = lambda tile: np.repeat(np.repeat(tile, 4, axis=0), 4, axis=1)  # noqa: E731
    result, engine = superres.upscale(image, runner=quadruple)
    assert result.size == (16, 16)
    assert result.getchannel("A").getpixel((0, 0)) == 128
    assert engine == {"tile": 256, "overlap": 16}
