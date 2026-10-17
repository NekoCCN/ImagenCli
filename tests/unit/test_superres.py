"""Super-resolution plumbing: tiling, providers and weight policy without downloads."""

import pytest

pytest.importorskip("numpy")

import io

import numpy as np
from PIL import Image

from imagen_cli.application import superres
from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.database import connect
from imagen_cli.storage.repositories import Repositories


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
    monkeypatch.setitem(superres.MANIFEST, "manual", {
        "url": None, "sha256": None, "scale": 2, "license": "test", "source": "local",
    })
    with pytest.raises(ValueError, match="place manual.onnx"):
        superres.ensure_weights("manual")
    path = tmp_path / "manual.onnx"
    path.write_bytes(b"weights")
    assert superres.ensure_weights("manual") == path


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
    monkeypatch.setitem(superres.MANIFEST, "manual", {
        "url": None, "sha256": None, "scale": 2, "license": "test", "source": "local",
    })
    path = tmp_path / "manual.onnx"
    path.write_bytes(b"weights")
    superres.ensure_weights("manual")
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="digest mismatch"):
        superres.ensure_weights("manual")


def test_alpha_is_carried_through_upscaling():
    image = Image.new("RGBA", (4, 4), (30, 60, 90, 128))
    quadruple = lambda tile: np.repeat(np.repeat(tile, 4, axis=0), 4, axis=1)  # noqa: E731
    result, engine = superres.upscale(image, runner=quadruple)
    assert result.size == (16, 16)
    assert result.getchannel("A").getpixel((0, 0)) == 128
    assert engine == {"tile": 256, "overlap": 16}


def test_catalog_reports_each_model_and_availability(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    value = superres.catalog()
    names = {entry["model"] for entry in value["models"]}
    assert {"realesrgan-x4plus", "realesrgan-x2plus",
            "realesrgan-x4plus-anime", "swinir-s-x2", "swinir-s-x4"} <= names
    anime = next(item for item in value["models"]
                 if item["model"] == "realesrgan-x4plus-anime")
    assert anime["downloadable"] is True and anime["downloaded"] is False


def test_round_up_pads_to_the_model_multiple():
    assert superres._round_up(65, 8) == 72
    assert superres._round_up(64, 8) == 64
    assert superres._round_up(65, 1) == 65


def test_benchmark_compares_models_without_writing(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    repositories = Repositories(connect(root / "state.sqlite3"))
    store = ArtifactStore(root / "artifacts", repositories)
    image = Image.new("RGB", (32, 32))
    for y in range(32):
        for x in range(32):
            image.putpixel((x, y), ((x * 8) % 256, (y * 8) % 256, ((x ^ y) * 4) % 256))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    identity = "art_" + "d" * 32
    store.stage(identity, buffer.getvalue())
    store.publish(identity)

    def fake_upscale(source, *, model, **_kwargs):
        scale = int(superres.MANIFEST[model]["scale"])
        return source.resize(
            (source.width * scale, source.height * scale), Image.Resampling.NEAREST
        ), {"provider": "fake"}

    monkeypatch.setattr(superres, "upscale", fake_upscale)
    before = repositories.list("artifacts", limit=10)
    result = superres.benchmark(
        repositories, store, identity, ["realesrgan-x4plus", "realesrgan-x2plus"]
    )
    assert [item["model"] for item in result["results"]] == [
        "realesrgan-x4plus", "realesrgan-x2plus",
    ]
    for item in result["results"]:
        assert item["psnr"] > 0 and 0 < item["ssim"] <= 1
        assert item["duration_ms"] >= 0
    assert len(repositories.list("artifacts", limit=10)) == len(before)
    repositories.connection.close()
