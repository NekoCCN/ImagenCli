"""Structure/texture solvers must improve controlled inputs, not just run."""

import numpy as np
import pytest
from PIL import Image

from imagen_cli.application import media_ops


def test_structure_operators_are_available():
    assert {"l0_smooth", "domain_transform", "wls_smooth", "rtv_smooth"} <= media_ops.OPS.keys()


@pytest.mark.parametrize("op", ["l0_smooth", "domain_transform", "wls_smooth", "rtv_smooth"])
def test_structure_uniform_alpha_and_determinism(op):
    assert op in media_ops.OPS
    image = Image.new("RGBA", (12, 9), (90, 140, 180, 127))
    first = media_ops.OPS[op](image)
    second = media_ops.OPS[op](image)
    assert first.mode == "RGBA"
    assert first.tobytes() == second.tobytes()
    assert np.max(np.abs(np.asarray(first).astype(int) - np.asarray(image).astype(int))) <= 1


@pytest.mark.parametrize("op", ["l0_smooth", "domain_transform", "wls_smooth", "rtv_smooth"])
def test_structure_zero_strength_is_identity(op):
    assert op in media_ops.OPS
    image = Image.fromarray(np.random.default_rng(3).integers(0, 256, (9, 12, 3), dtype=np.uint8))
    assert media_ops.OPS[op](image, strength=0).tobytes() == image.tobytes()


@pytest.mark.parametrize("op", ["l0_smooth", "domain_transform", "wls_smooth", "rtv_smooth"])
def test_structure_reduces_small_flat_region_variation(op):
    assert op in media_ops.OPS
    array = np.full((16, 16, 3), 120, dtype=np.uint8)
    array[::2, ::2] = 126
    result = np.asarray(media_ops.OPS[op](Image.fromarray(array))).astype(float)
    assert result.var() < array.var()


@pytest.mark.parametrize("op", ["l0_smooth", "domain_transform", "wls_smooth", "rtv_smooth"])
def test_structure_rejects_invalid_strength(op):
    assert op in media_ops.OPS
    with pytest.raises(ValueError):
        media_ops.OPS[op](Image.new("RGB", (4, 4)), strength=float("nan"))


@pytest.mark.parametrize("op", ["l0_smooth", "domain_transform", "wls_smooth", "rtv_smooth"])
def test_hidden_rgb_cannot_change_visible_structure_pixels(op):
    first = np.full((8, 8, 4), (100, 120, 140, 255), dtype=np.uint8)
    first[:, :4] = (0, 0, 0, 0)
    second = first.copy()
    second[:, :4, :3] = (255, 20, 240)
    a = np.asarray(media_ops.OPS[op](Image.fromarray(first)))
    b = np.asarray(media_ops.OPS[op](Image.fromarray(second)))
    np.testing.assert_array_equal(a[:, 4:], b[:, 4:])
    np.testing.assert_array_equal(a[:, :4], first[:, :4])
    np.testing.assert_array_equal(b[:, :4], second[:, :4])
