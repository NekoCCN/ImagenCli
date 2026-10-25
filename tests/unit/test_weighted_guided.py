"""WGIF variance weighting, opacity isolation and edge-preservation fixtures."""

import pytest
from PIL import Image

pytest.importorskip("numpy")
import numpy as np

from imagen_cli.application import media_ops
from imagen_cli.application.filters_advanced import guided_smooth


def apply(image, **params):
    return media_ops.OPS["weighted_guided_smooth"](image, **params)


def test_weighted_guided_records_variant_and_shared_defaults():
    from imagen_cli.application.operator_catalog import PRESET_PIPELINES
    from imagen_cli.application.postprocess_service import resolve_steps

    step = resolve_steps(PRESET_PIPELINES["weighted_guided_smooth"])[0]
    assert step["effective_params"]["radius"] == 4
    assert step["approximation"] == "luma_coverage_sigma1_variance_weights"
    assert step["status"] == "experimental"


def test_weighted_guided_retains_step_better_than_fixed_regularization():
    values = np.full((32, 64, 3), 40, dtype=np.uint8)
    values[:, 32:] = 210
    image = Image.fromarray(values)
    fixed = np.asarray(guided_smooth(image, radius=8, epsilon=0.02, strength=1))
    weighted = np.asarray(apply(image, radius=8, epsilon=0.02, strength=1))
    assert np.abs(weighted[:, 30:34].astype(float) - values[:, 30:34]).mean() < np.abs(
        fixed[:, 30:34].astype(float) - values[:, 30:34]
    ).mean()


def test_weighted_guided_hidden_rgb_does_not_affect_visible_pixels():
    first = Image.new("RGBA", (24, 16), (10, 30, 60, 255))
    second = first.copy()
    for y in range(16):
        for x in range(12):
            first.putpixel((x, y), (255, 0, 0, 0))
            second.putpixel((x, y), (0, 255, 255, 0))
    one, two = np.asarray(apply(first)), np.asarray(apply(second))
    assert np.array_equal(one[:, 12:], two[:, 12:])
    assert np.array_equal(one[..., 3], np.asarray(first)[..., 3])
    assert np.array_equal(one[:, :12], np.asarray(first)[:, :12])


def test_weighted_guided_zero_strength_is_identity():
    image = Image.fromarray(np.random.default_rng(42).integers(0, 256, (16, 16, 3), dtype=np.uint8))
    assert apply(image, strength=0).tobytes() == image.tobytes()


@pytest.mark.parametrize("size", [(1, 1), (1, 8), (8, 1), (16, 16)])
def test_weighted_guided_constant_is_identity(size):
    image = Image.new("RGBA", size, (100, 70, 90, 128))
    assert apply(image).tobytes() == image.tobytes()


@pytest.mark.parametrize("params", [{"radius": True}, {"epsilon": 0}, {"strength": 2}])
def test_weighted_guided_rejects_bad_parameters(params):
    with pytest.raises(ValueError):
        apply(Image.new("RGB", (16, 16)), **params)
