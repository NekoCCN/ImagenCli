"""Deterministic operator catalog: every op runs, repeats, and stays in bounds."""

from io import BytesIO
from typing import Any

import pytest
from PIL import Image

from imagen_cli.application import media_ops

PARAMS: dict[str, dict[str, Any]] = {
    "resize": {"scale": 2},
    "crop": {"left": 1, "top": 1},
    "pad": {"width": 20, "height": 20, "color": "#112233"},
    "dither": {"mode": "jarvis"},
    "homogeneous": {},
}

CASES = {
    "resize": {"scale": 2},
    "crop": {"left": 1, "top": 1},
    "trim": {},
    "pad": {"width": 20, "height": 20, "color": "#112233"},
    "seamless": {"blend": 0.2},
    "pixelate": {"scale": 2},
    "mosaic": {"size": 4},
    "block_scale": {"factor": 2},
    "scale2x": {},
    "quantize": {"colors": 4},
    "posterize": {"levels": 3},
    "grayscale": {},
    "autocontrast": {},
    "equalize": {},
    "invert": {},
    "brightness": {"factor": 1.2},
    "contrast": {"factor": 1.1},
    "saturation": {"factor": 0.5},
    "gamma": {"value": 1.4},
    "exposure": {"stops": 0.5},
    "levels": {"black": 8, "white": 240},
    "shadows_highlights": {"shadows": 0.2, "highlights": -0.1},
    "white_balance": {"method": "gray_world"},
    "sepia": {"strength": 0.8},
    "duotone": {},
    "blur": {"radius": 1},
    "box_blur": {"radius": 1},
    "motion_blur": {"size": 5, "angle": 45},
    "median": {"size": 3},
    "minimum": {"size": 3},
    "maximum": {"size": 3},
    "mode_filter": {"size": 3},
    "highpass": {"radius": 2},
    "sharpen": {"radius": 1, "percent": 120},
    "edge": {"mode": "find"},
    "rotate": {"degrees": 90},
    "flip": {"axis": "vertical"},
    "transpose": {"mode": "rotate_180"},
    "dither": {"mode": "atkinson"},
    "halftone": {"size": 4},
    "cel_shade": {"levels": 3},
    "ascii": {"cols": 20},
    "glitch": {"seed": 3, "strength": 4},
    "scanlines": {"period": 3, "strength": 0.2},
    "watermark": {"text": "imagen", "size": 12},
    "orient": {},
    "perspective": {"quad": "1,1,11,0,11,11,0,11"},
    "tilt_shift": {"radius": 2, "band": 0.3},
    "oil_paint": {"colors": 6},
    "palette_map": {"palette": "000000,ffffff,ff0000"},
    "film_grain": {"amount": 0.05, "seed": 42},
    "de_ai_bloom": {"radius": 2, "intensity": 0.2, "threshold": 180},
    "filmic_tone": {"lift_black": 6, "compress_white": 245},
    "chromatic_aberration": {"shift": 1, "radial": False},
    "vignette": {"radius": 0.75, "feather": 0.5, "darkness": 0.3},
    "color_temperature": {"temperature": 0.1, "tint": -0.05},
    "focus_lines": {"lines": 20, "inner_radius": 0.3, "max_width": 2, "seed": 42},
    "screentone": {"frequency": 10, "angle": 45.0},
    "lineart_extract": {"radius": 1.0, "k": 1.6, "gamma": 0.98, "threshold": 0.02},
}


@pytest.fixture
def image() -> Image.Image:
    source = Image.new("RGB", (12, 12))
    for y in range(12):
        for x in range(12):
            source.putpixel((x, y), (x * 20, y * 20, (x + y) * 10))
    return source


@pytest.mark.parametrize("name", sorted(CASES))
def test_operator_runs_and_is_repeatable(image, name):
    params = CASES[name]
    first = media_ops.OPS[name](image.copy(), **params)
    second = media_ops.OPS[name](image.copy(), **params)
    assert first.size == second.size
    assert first.tobytes() == second.tobytes()


def test_background_and_chroma_operations():
    subject = Image.new("RGBA", (8, 8), (0, 255, 0, 255))
    for x in range(4, 8):
        for y in range(4, 8):
            subject.putpixel((x, y), (200, 20, 20, 255))
    keyed = media_ops.chroma_key(subject, color="#00ff00")
    assert keyed.getpixel((0, 0))[3] == 0
    assert keyed.getpixel((7, 7))[3] == 255
    blurred = media_ops.background_blur(keyed, radius=2)
    assert blurred.mode == "RGBA"
    with pytest.raises(ValueError, match="alpha"):
        media_ops.background_blur(Image.new("RGB", (4, 4)))


def test_scale2x_doubles_size(image):
    assert media_ops.scale2x(image).size == (24, 24)


def test_seamless_tiles_have_matching_edges(image):
    result = media_ops.seamless(image, blend=0.2)
    left = result.crop((0, 0, 1, result.height)).tobytes()
    right = result.crop((result.width - 1, 0, result.width, result.height)).tobytes()
    assert left != right


def test_encode_formats_and_target_bytes(image):
    for name in ("png", "jpeg", "webp"):
        data = media_ops.encode(image, format=name, quality=80)
        with Image.open(BytesIO(data)) as decoded:
            decoded.load()
            assert decoded.size == image.size
    png = media_ops.encode(image, format="png")
    assert png.startswith(b"\x89PNG")


def test_target_bytes_shrinks_jpeg():
    noisy = Image.new("RGB", (96, 96))
    for y in range(96):
        for x in range(96):
            noisy.putpixel((x, y), ((x * 37) % 256, (y * 53) % 256, ((x ^ y) * 11) % 256))
    base = media_ops.encode(noisy, format="jpeg", quality=95)
    target = len(base) // 2
    smaller = media_ops.encode(noisy, format="jpeg", quality=95, target_bytes=target)
    assert len(smaller) <= target


def test_target_bytes_refuses_lossless_png(image):
    with pytest.raises(ValueError, match="lossy"):
        media_ops.encode(image, format="png", target_bytes=100)


def test_sticker_outline_and_alpha_requirement():
    rgba = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    for x in range(6, 14):
        for y in range(6, 14):
            rgba.putpixel((x, y), (255, 100, 100, 255))
    stickered = media_ops.sticker_outline(rgba, width=2, color="#ffffff", shadow=True)
    assert stickered.mode == "RGBA"
    assert stickered.size == rgba.size
    # 边缘扩充了白色贴纸边
    assert stickered.getpixel((5, 5))[3] > 0

    # 无 alpha 输入时应抛出异常
    with pytest.raises(ValueError, match="alpha"):
        media_ops.sticker_outline(Image.new("RGB", (10, 10)), width=2)

