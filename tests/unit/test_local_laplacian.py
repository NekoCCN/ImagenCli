"""Sampled local-Laplacian reconstruction and transparency regressions."""

import pytest
from PIL import Image

pytest.importorskip("numpy")
import numpy as np

from imagen_cli.application import media_ops


def apply(image, **params):
    return media_ops.OPS["local_laplacian_sampled"](image, **params)


def test_local_laplacian_records_sampled_identity():
    from imagen_cli.application.operator_catalog import PRESET_PIPELINES
    from imagen_cli.application.postprocess_service import resolve_steps

    step = resolve_steps(PRESET_PIPELINES["local_laplacian_sampled"])[0]
    assert step["approximation"] == "sampled_remap_gaussian_bilinear_pyramid"
    assert step["alpha_policy"] == "preserve_alpha_mean_extend_hidden_rgb"


def test_local_laplacian_identity_remap_is_exact():
    image = Image.fromarray(np.random.default_rng(3).integers(0, 256, (25, 31, 3), dtype=np.uint8))
    assert apply(image, detail=1).tobytes() == image.tobytes()


def test_local_laplacian_reduces_small_checker_texture():
    texture = np.indices((32, 32)).sum(0) % 2 * 16 + 120
    image = Image.fromarray(np.repeat(texture[..., None], 3, 2).astype(np.uint8))
    result = np.asarray(apply(image, detail=2, sigma=0.3, strength=1))
    assert result.std() < np.asarray(image).std() * 0.7


def test_local_laplacian_constant_and_odd_size():
    image = Image.new("RGBA", (17, 23), (80, 100, 110, 128))
    assert apply(image).tobytes() == image.tobytes()


def test_local_laplacian_hidden_rgb_isolated():
    first = Image.new("RGBA", (17, 23), (80, 100, 110, 255))
    second = first.copy()
    for y in range(23):
        for x in range(8):
            first.putpixel((x, y), (255, 0, 0, 0))
            second.putpixel((x, y), (0, 255, 0, 0))
    one, two = np.asarray(apply(first)), np.asarray(apply(second))
    assert np.array_equal(one[:, 8:], two[:, 8:])
    assert np.array_equal(one[:, :8], np.asarray(first)[:, :8])


@pytest.mark.parametrize("params", [{"detail": 0}, {"samples": True}, {"sigma": 0},
                                    {"levels": 0}, {"strength": float("nan")}])
def test_local_laplacian_rejects_invalid_parameters(params):
    with pytest.raises(ValueError):
        apply(Image.new("RGB", (16, 16)), **params)
