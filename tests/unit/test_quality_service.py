"""PSNR/SSIM command over published artifacts."""

from pathlib import Path

import pytest
from PIL import Image

from imagen_cli.application import quality_service
from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.database import connect
from imagen_cli.storage.repositories import Repositories


@pytest.fixture
def workspace(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    repositories = Repositories(connect(root / "state.sqlite3"))
    store = ArtifactStore(root / "artifacts", repositories)
    for seed, shade in (("a", 40), ("b", 120)):
        image = Image.new("RGB", (8, 8), (shade, shade, shade))
        identity = "art_" + seed * 32
        store.stage(identity, _png(image))
        store.publish(identity)
    yield repositories, store
    repositories.connection.close()


def _png(image: Image.Image) -> bytes:
    from io import BytesIO

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_compare_reports_scores(workspace):
    repositories, store = workspace
    result = quality_service.compare(
        repositories, store, "art_" + "a" * 32, "art_" + "a" * 32
    )
    assert result["identical"] is True
    assert result["psnr"] is None
    assert result["ssim"] == 1.0
    assert result["ssim_metric"] == "local_gaussian_pillow_l_v1"
    different = quality_service.compare(
        repositories, store, "art_" + "a" * 32, "art_" + "b" * 32
    )
    assert different["identical"] is False
    assert different["psnr"] is not None
    assert 0 < different["ssim"] < 1


def test_compare_requires_matching_dimensions(workspace):
    repositories, store = workspace
    image = Image.new("RGB", (4, 4), (10, 10, 10))
    store.stage("art_" + "c" * 32, _png(image))
    store.publish("art_" + "c" * 32)
    with pytest.raises(ValueError, match="dimensions"):
        quality_service.compare(
            repositories, store, "art_" + "a" * 32, "art_" + "c" * 32
        )
