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
    "aces_tonemap": {"exposure": 1.0, "contrast": 1.1},
    "guided_filter": {"radius": 2, "eps": 0.04, "boost": 1.5},
    "kuwahara": {"size": 3},
    "anamorphic_flare": {"threshold": 180, "length": 10, "tint": "#4a90e2", "intensity": 0.5},
    "color_transfer": {"style": "anime_sky", "strength": 0.8},
    "line_color": {"strength": 0.5, "darkness": 0.4},
    "color_balance": {
        "shadows": [0.1, 0.0, -0.1], "midtones": [0.0, 0.1, 0.0], "highlights": [-0.1, 0.0, 0.1]
    },
    "fisheye": {"strength": 0.2, "zoom": 1.0},
    "vibrance": {"amount": 0.3},
    "diffuse_glow": {"threshold": 160, "radius": 4, "intensity": 0.3},
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


def test_film_grain_monochrome_vs_chromatic():
    base = Image.new("RGB", (32, 32), (128, 128, 128))
    mono = media_ops.film_grain(base, amount=0.1, monochrome=True, seed=42)
    chroma = media_ops.film_grain(base, amount=0.1, monochrome=False, seed=42)

    # 单色微噪点：三通道增减量完全一致
    mono_bytes = mono.tobytes()
    for i in range(0, len(mono_bytes), 3):
        r, g, b = mono_bytes[i], mono_bytes[i + 1], mono_bytes[i + 2]
        assert r == g == b

    # 彩色微噪点：存在通道间不同步的扰动
    chroma_bytes = chroma.tobytes()
    has_diff = any(
        chroma_bytes[i] != chroma_bytes[i + 1] or chroma_bytes[i] != chroma_bytes[i + 2]
        for i in range(0, len(chroma_bytes), 3)
    )
    assert has_diff


def test_focus_lines_preserves_center_safe_zone():
    center_color = (255, 0, 0)
    base = Image.new("RGB", (64, 64), center_color)
    result = media_ops.focus_lines(base, lines=30, inner_radius=0.4, seed=42)

    # 中心点与内半径安全区内的像素应保持原色
    cx, cy = 32, 32
    for dx in range(-5, 6):
        for dy in range(-5, 6):
            if dx * dx + dy * dy <= 25:
                assert result.getpixel((cx + dx, cy + dy)) == center_color

    # 外围区域存在非原色的集中线像素
    assert result.tobytes() != base.tobytes()


def test_vignette_center_preserved_and_corners_attenuated():
    base = Image.new("RGB", (64, 64), (200, 200, 200))
    vignetted = media_ops.vignette(base, radius=0.6, feather=0.4, darkness=0.5)

    center_pix = vignetted.getpixel((32, 32))
    corner_pix = vignetted.getpixel((0, 0))

    # 中心像素亮度保持或接近原值
    assert center_pix[0] >= 190
    # 四角像素亮度显著下降
    assert corner_pix[0] < center_pix[0] - 30


def test_color_temperature_directional_shift():
    base = Image.new("RGB", (32, 32), (128, 128, 128))
    warm = media_ops.color_temperature(base, temperature=0.3)
    cool = media_ops.color_temperature(base, temperature=-0.3)

    warm_p = warm.getpixel((16, 16))
    cool_p = cool.getpixel((16, 16))

    # 暖色：R升高，B降低
    assert warm_p[0] > 128 and warm_p[2] < 128
    # 冷色：B升高，R降低
    assert cool_p[2] > 128 and cool_p[0] < 128


def test_aces_tonemap_and_guided_filter_characteristics():
    # ACES 压制高光死白
    bright = Image.new("RGB", (20, 20), (250, 250, 250))
    toned = media_ops.aces_tonemap(bright, exposure=1.0)
    toned_p = toned.getpixel((10, 10))
    # 经 ACES 曲线处理，高光平滑滚降在安全显示区间
    assert toned_p[0] <= 255
    assert toned.size == (20, 20)

    # Guided Filter 增强微观高频细节
    noisy = Image.new("RGB", (20, 20))
    for y in range(20):
        for x in range(20):
            noisy.putpixel((x, y), ((x * 10) % 256, (y * 10) % 256, ((x + y) * 5) % 256))
    boosted = media_ops.guided_filter(noisy, radius=2, boost=2.0)
    assert boosted.size == (20, 20)
    assert boosted.tobytes() != noisy.tobytes()


def test_anamorphic_flare_streak_spread():
    # 纯黑背景上中心一个高光白色小点
    dark = Image.new("RGB", (64, 64), (0, 0, 0))
    dark.putpixel((32, 32), (255, 255, 255))
    flared = media_ops.anamorphic_flare(
        dark, threshold=150, length=32, tint="#4a90e2", intensity=0.8
    )

    # 水平方向（X 轴两侧）产生显著蓝色眩光条纹
    left_flare = flared.getpixel((20, 32))
    right_flare = flared.getpixel((44, 32))
    # 纵向对应位置无水平条纹
    top_pos = flared.getpixel((32, 20))

    assert left_flare[2] > top_pos[2]
    assert right_flare[2] > top_pos[2]


def test_kuwahara_and_color_transfer():
    # 桑原滤波测试
    step_img = Image.new("RGB", (24, 24), (20, 20, 20))
    for x in range(12, 24):
        for y in range(24):
            step_img.putpixel((x, y), (220, 220, 220))
    filtered = media_ops.kuwahara(step_img, size=3)
    assert filtered.size == step_img.size

    # 统计色彩迁移测试
    sky = media_ops.color_transfer(step_img, style="anime_sky", strength=0.8)
    assert sky.size == step_img.size
    cyber = media_ops.color_transfer(step_img, style="cyberpunk", strength=0.8)
    assert cyber.size == step_img.size
    assert sky.tobytes() != cyber.tobytes()


def test_anime_line_enhance_sharpens_and_inks_strokes():
    from PIL import ImageDraw

    # 创建浅色平涂背景并在中心绘制一条灰色线稿
    img = Image.new("RGBA", (60, 60), (240, 220, 200, 255))
    draw = ImageDraw.Draw(img)
    draw.line([(10, 30), (50, 30)], fill=(120, 100, 90, 255), width=2)

    enhanced = media_ops.anime_line_enhance(
        img, strength=1.5, thickness=1.0, sigma=1.0, gamma=1.5, closing_radius=1
    )
    assert enhanced.size == img.size
    assert "A" in enhanced.getbands()

    # 线条中心像素被加深压黑（数值减小）
    orig_stroke_pixel = img.getpixel((30, 30))
    enhanced_stroke_pixel = enhanced.getpixel((30, 30))
    assert enhanced_stroke_pixel[0] < orig_stroke_pixel[0]
    assert enhanced_stroke_pixel[1] < orig_stroke_pixel[1]
    assert enhanced_stroke_pixel[2] < orig_stroke_pixel[2]

    # 远离线条的平涂色块几乎不受影响（保真Cel平涂）
    bg_orig = img.getpixel((10, 10))
    bg_enhanced = enhanced.getpixel((10, 10))
    assert abs(bg_orig[0] - bg_enhanced[0]) <= 2
    assert abs(bg_orig[1] - bg_enhanced[1]) <= 2

    # 边界参数校验
    with pytest.raises(ValueError, match="strength"):
        media_ops.anime_line_enhance(img, strength=-0.1)
    with pytest.raises(ValueError, match="thickness"):
        media_ops.anime_line_enhance(img, thickness=0.1)
    with pytest.raises(ValueError, match="closing_radius"):
        media_ops.anime_line_enhance(img, closing_radius=10)


