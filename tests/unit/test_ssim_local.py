"""Local SSIM must distinguish distortion location, not just global moments."""

import pytest
from PIL import Image

pytest.importorskip("numpy")
import numpy as np

from imagen_cli.application import filters_numpy


def test_ssim_weights_center_distortion_more_than_corner():
    reference = Image.new("L", (11, 11), 0)
    center = reference.copy()
    corner = reference.copy()
    center.putpixel((5, 5), 255)
    corner.putpixel((0, 0), 255)
    # One valid Gaussian window: equal global moments, unequal local weights.
    assert filters_numpy.ssim(reference, center) < filters_numpy.ssim(reference, corner) - 0.1


def test_ssim_constant_luminance_matches_formula():
    first = Image.new("L", (16, 16), 50)
    second = Image.new("L", (16, 16), 100)
    assert filters_numpy.ssim(first, second) == pytest.approx(10006.5025 / 12506.5025)


@pytest.mark.parametrize("size", [(1, 1), (2, 8), (8, 8), (11, 11), (32, 24)])
def test_ssim_identity_handles_small_images(size):
    image = Image.fromarray(np.random.default_rng(42).integers(0, 256, size[::-1], dtype=np.uint8))
    assert filters_numpy.ssim(image, image) == pytest.approx(1)


def test_ssim_rejects_different_dimensions():
    with pytest.raises(ValueError, match="dimensions"):
        filters_numpy.ssim(Image.new("L", (12, 12)), Image.new("L", (13, 12)))


def test_legacy_ssim_retains_global_moments():
    reference = Image.new("L", (11, 11), 0)
    center = reference.copy()
    corner = reference.copy()
    center.putpixel((5, 5), 255)
    corner.putpixel((0, 0), 255)
    assert filters_numpy.ssim_global_legacy(reference, center) == pytest.approx(
        filters_numpy.ssim_global_legacy(reference, corner)
    )
