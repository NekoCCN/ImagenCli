"""Unit tests for newly added advanced postprocessing operators."""

import numpy as np
from PIL import Image

from imagen_cli.application import media_ops


def _sample_image() -> Image.Image:
    # 64x64 test image with a mid-tone grey background (100, 100, 100)
    img = Image.new("RGB", (64, 64), (100, 100, 100))
    arr = np.array(img)
    # A colored region (e.g. red hair/cloth)
    arr[10:30, 10:30] = [220, 50, 50]
    # A black line simulating lineart
    arr[10:30, 30:32] = [10, 10, 10]
    # A dark shadow region
    arr[40:60, 10:30] = [30, 30, 30]
    # A bright highlight region
    arr[40:60, 40:60] = [240, 240, 240]
    return Image.fromarray(arr)


def test_line_color_softens_dark_lines():
    img = _sample_image()
    before_px = img.getpixel((30, 20))
    assert before_px[0] < 30

    res = media_ops.OPS["line_color"](img, strength=0.8, darkness=0.5)
    assert res.size == img.size
    assert res.mode == img.mode
    after_px = res.getpixel((30, 20))
    assert after_px[0] > before_px[0]


def test_color_balance_adjusts_shadows_and_highlights():
    img = _sample_image()
    shadow_before = img.getpixel((20, 50))
    high_before = img.getpixel((50, 50))

    res = media_ops.OPS["color_balance"](
        img,
        shadows=[0.0, 0.0, 0.3],
        highlights=[0.3, 0.0, 0.0],
    )
    shadow_after = res.getpixel((20, 50))
    high_after = res.getpixel((50, 50))

    assert shadow_after[2] > shadow_before[2]
    assert high_after[0] > high_before[0]


def test_fisheye_distortion():
    img = _sample_image()
    res = media_ops.OPS["fisheye"](img, strength=0.4, zoom=1.0)
    assert res.size == img.size
    assert res.mode == img.mode
    center_before = img.getpixel((32, 32))
    center_after = res.getpixel((32, 32))
    assert np.allclose(center_before, center_after, atol=10)


def test_vibrance_protects_saturated_and_boosts_muted():
    arr = np.zeros((32, 32, 3), dtype=np.uint8)
    arr[:, :16] = [100, 110, 120]  # low saturation
    arr[:, 16:] = [240, 10, 10]   # high saturation
    img = Image.fromarray(arr)

    res = media_ops.OPS["vibrance"](img, amount=0.5)
    res_arr = np.array(res)

    low_sat_diff_before = abs(int(arr[10, 5, 0]) - int(arr[10, 5, 2]))
    low_sat_diff_after = abs(int(res_arr[10, 5, 0]) - int(res_arr[10, 5, 2]))
    assert low_sat_diff_after > low_sat_diff_before

    assert res_arr[10, 20, 0] <= 255


def test_diffuse_glow_adds_bloom_to_highlights():
    img = _sample_image()
    outside_before = img.getpixel((39, 50))
    res = media_ops.OPS["diffuse_glow"](img, threshold=180, radius=5, intensity=0.6)
    outside_after = res.getpixel((39, 50))

    assert outside_after[0] >= outside_before[0]
    assert np.mean(outside_after) > np.mean(outside_before)


def test_anti_checkerboard_spectral_filter():
    # Construct an image with high-frequency checkerboard pattern
    arr = np.full((64, 64, 3), 128, dtype=np.uint8)
    arr[::2, ::2] = 200
    arr[1::2, 1::2] = 200
    img = Image.fromarray(arr)

    res = media_ops.OPS["anti_checkerboard"](img, strength=0.7, cutoff=0.7)
    assert res.size == img.size
    res_arr = np.array(res)
    # The variance/high frequency oscillation should be attenuated
    assert np.std(res_arr) < np.std(arr)


def test_subsurface_scattering_diffuses_skin_tones():
    # Warm peach/skin tone: YCbCr will fall into skin region
    # RGB (235, 185, 155) -> typical warm skin
    img = Image.new("RGB", (48, 48), (235, 185, 155))
    # Place a sharp dark spot in the middle
    arr = np.array(img)
    arr[20:28, 20:28] = [80, 50, 40]
    img = Image.fromarray(arr)

    res = media_ops.OPS["subsurface_scattering"](
        img, radius=4, scatter_strength=0.5, skin_only=True
    )
    assert res.size == img.size
    res_arr = np.array(res)
    # Center dark spot should be softened by diffused surrounding skin tone
    assert res_arr[24, 24, 0] > arr[24, 24, 0]


def test_de_haloing_suppresses_edge_overshoot():
    # Create image with a sharp edge that has white halo overshoot
    arr = np.full((32, 32, 3), 100, dtype=np.uint8)
    arr[:, 16:] = 50
    # Artificially inject bright overshoot halo line on the edge
    arr[:, 15] = 230
    img = Image.fromarray(arr)

    res = media_ops.OPS["de_haloing"](img, radius=2, strength=0.8, threshold=20)
    assert res.size == img.size
    res_arr = np.array(res)
    # The halo line (pixel 15) should be clamped and suppressed
    assert res_arr[16, 15, 0] < 200

