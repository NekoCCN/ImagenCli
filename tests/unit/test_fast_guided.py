"""Fast coefficient estimation preserves full-resolution guidance and Alpha."""

import numpy as np
import pytest
from PIL import Image

from imagen_cli.application import media_ops, postprocess_service
from imagen_cli.application.operator_catalog import get_operator_item


def test_subsample_one_matches_full_guided_filter():
    image = Image.fromarray(np.random.default_rng(7).integers(0, 256, (13, 17, 4), dtype=np.uint8))
    actual = media_ops.OPS["fast_guided_smooth"](image, radius=4, subsample=1)
    expected = media_ops.OPS["guided_smooth"](image, radius=4)
    assert actual.tobytes() == expected.tobytes()


@pytest.mark.parametrize("size", [(1, 7), (8, 1), (17, 13)])
def test_constant_and_alpha_are_preserved(size):
    image = Image.new("RGBA", size, (100, 120, 140, 128))
    assert media_ops.OPS["fast_guided_smooth"](image).tobytes() == image.tobytes()


def test_hidden_color_does_not_pollute_visible_output():
    first = np.full((13, 17, 4), (100, 120, 140, 255), dtype=np.uint8)
    first[:, :5] = (255, 0, 0, 0)
    second = first.copy()
    second[:, :5, :3] = (0, 255, 255)
    a = np.asarray(media_ops.OPS["fast_guided_smooth"](Image.fromarray(first)))
    b = np.asarray(media_ops.OPS["fast_guided_smooth"](Image.fromarray(second)))
    np.testing.assert_array_equal(a[:, 5:], b[:, 5:])
    np.testing.assert_array_equal(a[:, :5], first[:, :5])


def test_zero_strength_is_exact_bypass():
    image = Image.fromarray(np.random.default_rng(8).integers(0, 256, (9, 11, 4), dtype=np.uint8))
    assert media_ops.OPS["fast_guided_smooth"](image, strength=0).tobytes() == image.tobytes()


@pytest.mark.parametrize("params", [{"subsample": True}, {"subsample": 0},
    {"subsample": 2.5}, {"radius": 0}, {"epsilon": float("nan")}])
def test_invalid_parameters_rejected(params):
    with pytest.raises(ValueError):
        media_ops.OPS["fast_guided_smooth"](Image.new("RGB", (5, 5)), **params)


def test_catalog_and_provenance_identify_approximation():
    item = get_operator_item("fast_guided_smooth")
    assert item is not None
    step = postprocess_service.resolve_steps(item.build_pipeline())[0]
    assert step["approximation"] == "bilinear_subsampled_luma_coefficients"
    assert step["effective_params"]["subsample"] == 4


def test_smoothing_reduces_flat_texture_without_moving_strong_edge():
    pixels = np.full((32, 64, 3), 80, dtype=np.uint8)
    pixels[:, 32:] = 180
    pixels[::2, ::2] += 4
    result = np.asarray(media_ops.OPS["fast_guided_smooth"](
        Image.fromarray(pixels), radius=8, strength=1, epsilon=0.001,
    )).astype(float)
    assert result[8:24, 8:24].var() < pixels[8:24, 8:24].var()
    crossings = np.argmax(result[..., 0] >= 130, axis=1)
    np.testing.assert_array_equal(crossings, np.full(32, 32))


def test_fine_line_loss_is_measured_not_claimed_as_preserved():
    pixels = np.full((32, 64, 3), 180, dtype=np.uint8)
    pixels[:, 31] = 80
    image = Image.fromarray(pixels)
    full = np.asarray(media_ops.OPS["guided_smooth"](image, radius=8, strength=1))
    fast = np.asarray(media_ops.OPS["fast_guided_smooth"](image, radius=8, strength=1))
    # This negative fixture records an approximation limit, not a quality endorsement.
    assert fast[:, 31].mean() > full[:, 31].mean()
