"""Weighted rank filtering has distinct impulse and edge behavior from averaging."""

import numpy as np
import pytest
from PIL import Image

from imagen_cli.application import media_ops
from imagen_cli.application.operator_catalog import get_operator_item


def test_weighted_median_removes_isolated_impulse_with_broad_range():
    pixels = np.full((5, 5, 3), 100, dtype=np.uint8)
    pixels[2, 2] = 255
    output = media_ops.OPS["weighted_median"](Image.fromarray(pixels), radius=1, sigma_range=1)
    assert output.getpixel((2, 2)) == (100, 100, 100)


def test_weighted_median_preserves_strong_edge_with_narrow_range():
    pixels = np.zeros((5, 6, 3), dtype=np.uint8)
    pixels[:, 3:] = 255
    image = Image.fromarray(pixels)
    assert media_ops.OPS["weighted_median"](image, sigma_range=0.05).tobytes() == image.tobytes()


def test_hidden_rgb_is_excluded_and_preserved():
    pixels = np.full((5, 5, 4), (100, 120, 140, 255), dtype=np.uint8)
    pixels[:, :2] = (255, 0, 0, 0)
    image = Image.fromarray(pixels)
    result = np.asarray(media_ops.OPS["weighted_median"](image, sigma_range=1))
    np.testing.assert_array_equal(result, pixels)


def test_zero_strength_bypasses_exactly():
    image = Image.fromarray(np.random.default_rng(4).integers(0, 256, (4, 5, 4), dtype=np.uint8))
    assert media_ops.OPS["weighted_median"](image, strength=0).tobytes() == image.tobytes()


@pytest.mark.parametrize("params", [{"radius": 0}, {"radius": True},
    {"sigma_range": 0}, {"strength": float("nan")}])
def test_invalid_parameters_rejected(params):
    with pytest.raises(ValueError):
        media_ops.OPS["weighted_median"](Image.new("RGB", (3, 3)), **params)


def test_catalog_recipe_executes():
    item = get_operator_item("weighted_median")
    assert item is not None
    step = item.build_pipeline()[0]
    result = media_ops.OPS[step["op"]](Image.new("RGB", (5, 5), "red"), **step["params"])
    assert result.getpixel((2, 2)) == (255, 0, 0)
