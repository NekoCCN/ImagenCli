"""Deterministic local image operators; no network, no provider calls."""

from __future__ import annotations

import io
import math
import random
from collections.abc import Callable
from typing import Any, cast

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

BAYER = [
    [0, 8, 2, 10],
    [12, 4, 14, 6],
    [3, 11, 1, 9],
    [15, 7, 13, 5],
]

KERNELS: dict[str, list[tuple[int, int, float]]] = {
    "atkinson": [
        (1, 0, 1 / 8),
        (2, 0, 1 / 8),
        (-1, 1, 1 / 8),
        (0, 1, 1 / 8),
        (1, 1, 1 / 8),
        (0, 2, 1 / 8),
    ],
    "jarvis": [
        (1, 0, 7 / 48),
        (2, 0, 5 / 48),
        (-2, 1, 3 / 48),
        (-1, 1, 5 / 48),
        (0, 1, 7 / 48),
        (1, 1, 5 / 48),
        (2, 1, 3 / 48),
        (-2, 2, 1 / 48),
        (-1, 2, 3 / 48),
        (0, 2, 5 / 48),
        (1, 2, 3 / 48),
        (2, 2, 1 / 48),
    ],
    "stucki": [
        (1, 0, 8 / 42),
        (2, 0, 4 / 42),
        (-2, 1, 2 / 42),
        (-1, 1, 4 / 42),
        (0, 1, 8 / 42),
        (1, 1, 4 / 42),
        (2, 1, 2 / 42),
        (-2, 2, 1 / 42),
        (-1, 2, 2 / 42),
        (0, 2, 4 / 42),
        (1, 2, 2 / 42),
        (2, 2, 1 / 42),
    ],
    "burkes": [
        (1, 0, 8 / 32),
        (2, 0, 4 / 32),
        (-2, 1, 2 / 32),
        (-1, 1, 4 / 32),
        (0, 1, 8 / 32),
        (1, 1, 4 / 32),
        (2, 1, 2 / 32),
    ],
    "sierra": [
        (1, 0, 5 / 32),
        (2, 0, 3 / 32),
        (-2, 1, 2 / 32),
        (-1, 1, 4 / 32),
        (0, 1, 5 / 32),
        (1, 1, 4 / 32),
        (2, 1, 2 / 32),
        (-1, 2, 2 / 32),
        (0, 2, 3 / 32),
        (1, 2, 2 / 32),
    ],
}


def _rgba(image: Image.Image) -> Image.Image:
    return image.convert("RGBA" if "A" in image.getbands() else "RGB")


def _matrix(size: int) -> list[list[int]]:
    if size == 2:
        return [[0, 2], [3, 1]]
    if size == 4:
        return BAYER
    if size == 8:
        result = []
        for y in range(8):
            row = []
            for x in range(8):
                row.append((BAYER[y % 4][x % 4] << 2) | BAYER[y // 4][x // 4])
            result.append(row)
        return result
    raise ValueError("bayer size must be 2, 4 or 8")


def resize(
    image: Image.Image,
    *,
    width: int | None = None,
    height: int | None = None,
    scale: int | None = None,
    filter: str = "lanczos",
) -> Image.Image:
    filters = {
        "nearest": Image.Resampling.NEAREST,
        "box": Image.Resampling.BOX,
        "bilinear": Image.Resampling.BILINEAR,
        "bicubic": Image.Resampling.BICUBIC,
        "lanczos": Image.Resampling.LANCZOS,
    }
    if filter not in filters:
        raise ValueError("unknown resize filter")
    if scale is not None:
        if scale <= 0:
            raise ValueError("scale must be positive")
        return image.resize((image.width * scale, image.height * scale), filters[filter])
    if width is None and height is None:
        raise ValueError("resize needs width, height or scale")
    if width is None:
        assert height is not None
        width = round(image.width * (height / image.height))
    if height is None:
        height = round(image.height * (width / image.width))
    if width <= 0 or height <= 0:
        raise ValueError("target size must be positive")
    return image.resize((width, height), filters[filter])


def crop(
    image: Image.Image,
    *,
    left: int = 0,
    top: int = 0,
    right: int | None = None,
    bottom: int | None = None,
) -> Image.Image:
    box = (
        left,
        top,
        image.width if right is None else right,
        image.height if bottom is None else bottom,
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError("crop box is empty")
    return image.crop(box)


def trim(image: Image.Image, *, threshold: int = 8) -> Image.Image:
    """Keep the region that differs from the corner background."""
    if not 0 <= threshold <= 255:
        raise ValueError("threshold must be between 0 and 255")
    if "A" in image.getbands():
        box = image.getchannel("A").getbbox()
    else:
        rgb = image.convert("RGB")
        background = Image.new("RGB", rgb.size, rgb.getpixel((0, 0)))
        mask = (
            ImageChops.difference(rgb, background)
            .convert("L")
            .point(lambda value: 255 if value > threshold else 0)
        )
        box = mask.getbbox()
    if box is None:
        raise ValueError("image is uniformly one colour")
    return image.crop(box)


def pad(
    image: Image.Image, *, width: int | None = None, height: int | None = None, color: str = "0,0,0"
) -> Image.Image:
    target = (width or image.width, height or image.height)
    if target[0] < image.width or target[1] < image.height:
        raise ValueError("padding target is smaller than the image")
    fill = _colour(color) + ((0,) if image.mode == "RGBA" else ())
    canvas = Image.new(image.mode, target, fill)
    canvas.paste(image, ((target[0] - image.width) // 2, (target[1] - image.height) // 2))
    return canvas


def seamless(image: Image.Image, *, blend: float = 0.1) -> Image.Image:
    """Wrap by half so tile edges match, then feather the inner seam."""
    if not 0 <= blend <= 0.5:
        raise ValueError("blend must be between 0 and 0.5")
    width, height = image.size
    rolled = ImageChops.offset(image, width // 2, height // 2)
    margin = max(1, round(min(width, height) * blend))
    if margin * 2 >= min(width, height):
        return rolled
    for horizontal in (True, False):
        if horizontal:
            box = (0, height // 2 - margin, width, height // 2 + margin)
        else:
            box = (width // 2 - margin, 0, width // 2 + margin, height)
        band = rolled.crop(box).filter(ImageFilter.GaussianBlur(margin / 2))
        rolled.paste(band, box[:2])
    return rolled


def _colour(value: str) -> tuple[int, int, int]:
    text = value.strip().lstrip("#")
    if "," in text:
        parts = [int(part) for part in text.split(",")]
        if len(parts) == 3 and all(0 <= part <= 255 for part in parts):
            return (parts[0], parts[1], parts[2])
        raise ValueError("colour must be r,g,b")
    if len(text) == 3:
        return cast(tuple[int, int, int], tuple(int(char * 2, 16) for char in text))
    if len(text) == 6:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    raise ValueError("colour must be #rgb or #rrggbb or r,g,b")


def pixelate(image: Image.Image, *, scale: int = 4) -> Image.Image:
    if scale < 1:
        raise ValueError("scale must be positive")
    small = (max(1, image.width // scale), max(1, image.height // scale))
    return image.resize(small, Image.Resampling.BOX).resize(image.size, Image.Resampling.NEAREST)


def block_scale(image: Image.Image, *, factor: int = 2) -> Image.Image:
    if factor < 1:
        raise ValueError("factor must be positive")
    return image.resize((image.width * factor, image.height * factor), Image.Resampling.NEAREST)


def scale2x(image: Image.Image) -> Image.Image:
    """Scale2x/EPX edge-aware doubling; deterministic and dependency free."""
    source = image.convert("RGB")
    width, height = source.size
    data = source.load()
    assert data is not None
    target = Image.new("RGB", (width * 2, height * 2))
    out = target.load()
    assert out is not None
    for y in range(height):
        for x in range(width):
            centre = data[x, y]
            up = data[x, max(0, y - 1)]
            left = data[max(0, x - 1), y]
            right = data[min(width - 1, x + 1), y]
            down = data[x, min(height - 1, y + 1)]
            if up != down and left != right:
                top_left = left if left == up else centre
                top_right = up if up == right else centre
                bottom_left = left if left == down else centre
                bottom_right = down if down == right else centre
            else:
                top_left = top_right = bottom_left = bottom_right = centre
            out[2 * x, 2 * y] = top_left
            out[2 * x + 1, 2 * y] = top_right
            out[2 * x, 2 * y + 1] = bottom_left
            out[2 * x + 1, 2 * y + 1] = bottom_right
    return target


def quantize(
    image: Image.Image, *, colors: int = 16, method: str = "median_cut", dither: bool = False
) -> Image.Image:
    if not 2 <= colors <= 256:
        raise ValueError("colors must be between 2 and 256")
    if method not in {"median_cut", "octree"}:
        raise ValueError("unknown quantization method")
    dither_method = Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE
    converted = image.convert("RGB").quantize(
        colors=colors,
        method=Image.Quantize.MEDIANCUT if method == "median_cut" else Image.Quantize.FASTOCTREE,
        dither=dither_method,
    )
    return converted.convert("RGBA" if image.mode == "RGBA" else "RGB")


def posterize(image: Image.Image, *, levels: int = 4) -> Image.Image:
    if not 1 <= levels <= 8:
        raise ValueError("levels must be between 1 and 8")
    return ImageOps.posterize(image.convert("RGB"), levels)


def grayscale(image: Image.Image, **_: Any) -> Image.Image:
    return image.convert("L").convert("RGBA" if image.mode == "RGBA" else "RGB")


def autocontrast(image: Image.Image, *, cutoff: int = 0) -> Image.Image:
    if not 0 <= cutoff <= 100:
        raise ValueError("cutoff must be between 0 and 100")
    return ImageOps.autocontrast(image.convert("RGB"), cutoff=cutoff)


def equalize(image: Image.Image, **_: Any) -> Image.Image:
    return ImageOps.equalize(image.convert("RGB"))


def invert(image: Image.Image, **_: Any) -> Image.Image:
    return ImageOps.invert(image.convert("RGB"))


def brightness(image: Image.Image, *, factor: float = 1.0) -> Image.Image:
    if not 0 <= factor <= 4:
        raise ValueError("factor must be between 0 and 4")
    return ImageEnhance.Brightness(image.convert("RGB")).enhance(factor)


def contrast(image: Image.Image, *, factor: float = 1.0) -> Image.Image:
    if not 0 <= factor <= 4:
        raise ValueError("factor must be between 0 and 4")
    return ImageEnhance.Contrast(image.convert("RGB")).enhance(factor)


def saturation(image: Image.Image, *, factor: float = 1.0) -> Image.Image:
    if not 0 <= factor <= 4:
        raise ValueError("factor must be between 0 and 4")
    return ImageEnhance.Color(image.convert("RGB")).enhance(factor)


def gamma(image: Image.Image, *, value: float = 1.0) -> Image.Image:
    if not 0.1 <= value <= 5:
        raise ValueError("gamma must be between 0.1 and 5")
    lookup = [min(255, round(255 * (index / 255) ** (1 / value))) for index in range(256)]
    return image.convert("RGB").point(lookup * 3)


def exposure(image: Image.Image, *, stops: float = 0.0) -> Image.Image:
    if not -4 <= stops <= 4:
        raise ValueError("stops must be between -4 and 4")
    return brightness(image, factor=2.0**stops)


def levels(
    image: Image.Image, *, black: int = 0, white: int = 255, midtone: float = 1.0
) -> Image.Image:
    if not 0 <= black < white <= 255 or not 0.1 <= midtone <= 5:
        raise ValueError("invalid levels")
    lookup = []
    for index in range(256):
        value = (index - black) / (white - black)
        value = min(1.0, max(0.0, value)) ** (1 / midtone)
        lookup.append(round(value * 255))
    return image.convert("RGB").point(lookup * 3)


def shadows_highlights(
    image: Image.Image, *, shadows: float = 0.0, highlights: float = 0.0
) -> Image.Image:
    if not -1 <= shadows <= 1 or not -1 <= highlights <= 1:
        raise ValueError("shadows and highlights must be between -1 and 1")
    lookup = []
    for index in range(256):
        value = index / 255
        lifted = value + shadows * (1 - value) * value
        compressed = lifted + highlights * (1 - lifted) * (1 - lifted) * 0.5
        lookup.append(round(min(1.0, max(0.0, compressed)) * 255))
    return image.convert("RGB").point(lookup * 3)


def white_balance(image: Image.Image, *, method: str = "gray_world") -> Image.Image:
    if method not in {"gray_world", "max_rgb"}:
        raise ValueError("unknown white balance method")
    rgb = image.convert("RGB")
    channels = rgb.split()
    if method == "gray_world":
        means = [_mean(channel) for channel in channels]
        target = sum(means) / 3
        scales = [target / mean if mean else 1.0 for mean in means]
    else:
        maxima = [
            max(index for index, count in enumerate(channel.histogram()) if count)
            for channel in channels
        ]
        target = max(maxima)
        scales = [target / peak if peak else 1.0 for peak in maxima]
    balanced = [
        channel.point([min(255, round(index * scale)) for index in range(256)])
        for channel, scale in zip(channels, scales, strict=True)
    ]
    return Image.merge("RGB", balanced)


def _mean(channel: Image.Image) -> float:
    histogram = channel.histogram()
    total = sum(histogram)
    return sum(index * count for index, count in enumerate(histogram)) / total


def sepia(image: Image.Image, *, strength: float = 1.0) -> Image.Image:
    if not 0 <= strength <= 1:
        raise ValueError("strength must be between 0 and 1")
    gray = image.convert("L")
    toned = ImageOps.colorize(gray, black="#26160b", mid="#8a5a2b", white="#f0d9b5")
    return Image.blend(image.convert("RGB"), toned, strength)


def duotone(image: Image.Image, *, dark: str = "1a2b4a", light: str = "f2e2c4") -> Image.Image:
    return ImageOps.colorize(image.convert("L"), black="#" + dark, white="#" + light)


def blur(image: Image.Image, *, radius: float = 2.0) -> Image.Image:
    if not 0 <= radius <= 100:
        raise ValueError("radius must be between 0 and 100")
    return image.filter(ImageFilter.GaussianBlur(radius))


def box_blur(image: Image.Image, *, radius: float = 2.0) -> Image.Image:
    if not 0 <= radius <= 100:
        raise ValueError("radius must be between 0 and 100")
    return image.filter(ImageFilter.BoxBlur(radius))


def motion_blur(image: Image.Image, *, size: int = 9, angle: int = 0) -> Image.Image:
    if not 3 <= size <= 31 or size % 2 == 0:
        raise ValueError("motion blur size must be odd and between 3 and 31")
    if angle % 45 != 0:
        raise ValueError("motion blur angle must be a multiple of 45")
    centre = size // 2
    kernel = [0.0] * (size * size)
    for offset in range(-centre, centre + 1):
        if angle in {0, 180}:
            x, y = centre + offset, centre
        elif angle in {90, 270}:
            x, y = centre, centre + offset
        elif angle in {45, 225}:
            x, y = centre + offset, centre + offset
        else:
            x, y = centre - offset, centre + offset
        kernel[y * size + x] = 1 / size
    return image.filter(ImageFilter.Kernel((size, size), kernel, scale=1, offset=0))


def median(image: Image.Image, *, size: int = 3) -> Image.Image:
    if size not in {3, 5}:
        raise ValueError("median size must be 3 or 5")
    return image.filter(ImageFilter.MedianFilter(size))


def minimum(image: Image.Image, *, size: int = 3) -> Image.Image:
    if size not in {3, 5}:
        raise ValueError("size must be 3 or 5")
    return image.filter(ImageFilter.MinFilter(size))


def maximum(image: Image.Image, *, size: int = 3) -> Image.Image:
    if size not in {3, 5}:
        raise ValueError("size must be 3 or 5")
    return image.filter(ImageFilter.MaxFilter(size))


def mode_filter(image: Image.Image, *, size: int = 3) -> Image.Image:
    if size not in {3, 5}:
        raise ValueError("size must be 3 or 5")
    return image.filter(ImageFilter.ModeFilter(size))


def highpass(image: Image.Image, *, radius: float = 3.0, boost: float = 1.0) -> Image.Image:
    if not 0 <= radius <= 50 or not 0 < boost <= 4:
        raise ValueError("invalid highpass parameters")
    rgb = image.convert("RGB")
    low = rgb.filter(ImageFilter.GaussianBlur(radius))
    difference = ImageChops.difference(rgb, low).point(
        lambda value: min(255, round(value * boost + 128))
    )
    return difference


def unsharp(
    image: Image.Image, *, radius: float = 2.0, percent: int = 150, threshold: int = 3
) -> Image.Image:
    if not 0 <= radius <= 100 or not 0 <= percent <= 1000 or not 0 <= threshold <= 255:
        raise ValueError("invalid unsharp parameters")
    return image.filter(ImageFilter.UnsharpMask(radius, percent, threshold))


sharpen = unsharp


def edge(image: Image.Image, *, mode: str = "find") -> Image.Image:
    if mode == "find":
        return image.convert("RGB").filter(ImageFilter.FIND_EDGES)
    if mode == "contour":
        return image.convert("RGB").filter(ImageFilter.CONTOUR)
    if mode == "emboss":
        return image.convert("RGB").filter(ImageFilter.EMBOSS)
    raise ValueError("unknown edge mode")


def rotate(image: Image.Image, *, degrees: int = 90) -> Image.Image:
    if degrees % 90 != 0:
        raise ValueError("rotation must be a multiple of 90")
    return image.rotate(-degrees, expand=True)


def flip(image: Image.Image, *, axis: str = "horizontal") -> Image.Image:
    if axis == "horizontal":
        return ImageOps.mirror(image)
    if axis == "vertical":
        return ImageOps.flip(image)
    raise ValueError("axis must be horizontal or vertical")


def transpose(image: Image.Image, *, mode: str = "rotate_90") -> Image.Image:
    options = {
        "rotate_90": Image.Transpose.ROTATE_90,
        "rotate_180": Image.Transpose.ROTATE_180,
        "rotate_270": Image.Transpose.ROTATE_270,
        "transpose": Image.Transpose.TRANSPOSE,
    }
    if mode not in options:
        raise ValueError("unknown transpose mode")
    return image.transpose(options[mode])


def dither(
    image: Image.Image,
    *,
    mode: str = "fs",
    colors: int = 16,
    levels: int = 2,
    size: int = 4,
    seed: int = 0,
) -> Image.Image:
    if not 2 <= levels <= 8:
        raise ValueError("levels must be between 2 and 8")
    algorithms = {"fs", "bayer", "atkinson", "jarvis", "stucki", "burkes", "sierra", "random"}
    if mode not in algorithms:
        raise ValueError("unknown dither mode")
    source = image.convert("L")
    width, height = source.size
    values = [[cast(int, source.getpixel((x, y))) for x in range(width)] for y in range(height)]
    step = 255 // (levels - 1)
    matrix = _matrix(size) if mode == "bayer" else None
    kernel = KERNELS.get(mode)
    generator = random.Random(seed)
    for y in range(height):
        for x in range(width):
            old = values[y][x]
            new = min(255, max(0, round(old / step) * step))
            values[y][x] = new
            if matrix is not None:
                threshold = (matrix[y % size][x % size] / (size * size) - 0.5) * step
                values[y][x] = min(255, max(0, round(new + threshold)))
                continue
            if mode == "random":
                values[y][x] = min(255, max(0, round(new + (generator.random() - 0.5) * step)))
                continue
            error = old - new
            for dx, dy, factor in kernel or KERNELS["fs"]:
                nx, ny = x + dx, y + dy
                if 0 <= nx < width and 0 <= ny < height:
                    carried = error * factor
                    values[ny][nx] = max(0, min(255, values[ny][nx] + round(carried)))
    for y in range(height):
        for x in range(width):
            source.putpixel((x, y), values[y][x])
    if colors > 2:
        return source.convert("RGB")
    return source.convert("1").convert("RGB")


def mosaic(image: Image.Image, *, size: int = 8) -> Image.Image:
    if size < 2:
        raise ValueError("size must be at least 2")
    small = (max(1, image.width // size), max(1, image.height // size))
    return image.resize(small, Image.Resampling.BOX).resize(image.size, Image.Resampling.NEAREST)


def halftone(image: Image.Image, *, size: int = 4, mode: str = "dot") -> Image.Image:
    if not 2 <= size <= 32:
        raise ValueError("size must be between 2 and 32")
    if mode not in {"dot", "line"}:
        raise ValueError("unknown halftone mode")
    gray = image.convert("L")
    pixels = gray.load()
    assert pixels is not None
    matrix = _matrix(8)
    output = Image.new("RGB", image.size, (255, 255, 255))
    target = output.load()
    assert target is not None
    for y in range(image.height):
        for x in range(image.width):
            if mode == "line":
                value = 255 if y % size else 0
            else:
                threshold = (matrix[(y * 8 // size) % 8][(x * 8 // size) % 8] + 0.5) * 255 / 64
                value = 0 if cast(int, pixels[x, y]) < threshold else 255
            target[x, y] = (value, value, value)
    return output


def cel_shade(image: Image.Image, *, levels: int = 3, edge_size: int = 3) -> Image.Image:
    if not 2 <= levels <= 6 or not 1 <= edge_size <= 9:
        raise ValueError("invalid cel-shade parameters")
    flat = posterize(blur(image, radius=1.0), levels=levels)
    edges = (
        image.convert("L")
        .filter(ImageFilter.FIND_EDGES)
        .point(lambda value: 0 if value > 24 else 255)
    )
    if edge_size > 1:
        edges = edges.filter(ImageFilter.MinFilter(edge_size))
    outline = Image.new("RGB", image.size, (20, 20, 24))
    return Image.composite(outline, flat, edges.point(lambda value: 255 - value))


def ascii_art(image: Image.Image, *, cols: int = 80, ramp: str = " .:-=+*#%@") -> Image.Image:
    """Render a text-art still as an image; artifacts remain media, not text."""
    if not 1 <= cols <= 400 or len(ramp) < 2:
        raise ValueError("invalid ascii parameters")
    gray = image.convert("L")
    rows = max(1, round(gray.height / gray.width * cols * 0.5))
    small = gray.resize((cols, rows), Image.Resampling.BOX)
    characters = []
    for y in range(rows):
        line = "".join(
            ramp[min(len(ramp) - 1, cast(int, small.getpixel((x, y))) * len(ramp) // 256)]
            for x in range(cols)
        )
        characters.append(line)
    font = ImageFont.load_default(size=14)
    cell = cast(tuple[int, int, int, int], font.getbbox("M"))
    cell_width = max(1, cell[2] - cell[0])
    cell_height = max(1, cell[3] - cell[1]) + 2
    canvas = Image.new("RGB", (cols * cell_width + 8, rows * cell_height + 8), (12, 12, 12))
    draw = ImageDraw.Draw(canvas)
    for index, line in enumerate(characters):
        draw.text((4, 4 + index * cell_height), line, fill=(230, 230, 230), font=font)
    return canvas


def glitch(image: Image.Image, *, seed: int = 0, strength: int = 8) -> Image.Image:
    if not 1 <= strength <= 64:
        raise ValueError("strength must be between 1 and 64")
    rgb = image.convert("RGB")
    generator = random.Random(seed)
    red, green, blue = rgb.split()
    red = ImageChops.offset(red, generator.randint(-strength, strength), 0)
    blue = ImageChops.offset(blue, generator.randint(-strength, strength), 0)
    merged = Image.merge("RGB", (red, green, blue))
    rows = merged.load()
    assert rows is not None
    for _ in range(max(1, image.height // 32)):
        start = generator.randrange(image.height)
        height = min(image.height - start, generator.randint(1, max(1, image.height // 16)))
        shift = generator.randint(-strength, strength)
        band = merged.crop((0, start, image.width, start + height))
        band = ImageChops.offset(band, shift, 0)
        merged.paste(band, (0, start))
    return merged


def scanlines(image: Image.Image, *, period: int = 3, strength: float = 0.2) -> Image.Image:
    if not 2 <= period <= 16 or not 0 <= strength <= 1:
        raise ValueError("invalid scanline parameters")
    overlay = Image.new("L", image.size, 255)
    pixels = overlay.load()
    assert pixels is not None
    for y in range(image.height):
        if y % period == 0:
            for x in range(image.width):
                pixels[x, y] = round(255 * (1 - strength))
    return Image.composite(Image.new("RGB", image.size, (0, 0, 0)), image.convert("RGB"), overlay)


def watermark(
    image: Image.Image,
    *,
    text: str = "imagen",
    size: int = 32,
    position: str = "bottom-right",
    opacity: float = 0.6,
) -> Image.Image:
    if not text or len(text) > 200 or not 8 <= size <= 256:
        raise ValueError("invalid watermark parameters")
    if not 0 <= opacity <= 1:
        raise ValueError("opacity must be between 0 and 1")
    base = image.convert("RGBA")
    font = ImageFont.load_default(size=size)
    box = cast(tuple[int, int, int, int], font.getbbox(text))
    width, height = box[2] - box[0], box[3] - box[1]
    margin = max(8, size // 2)
    if position == "bottom-right":
        origin = (base.width - width - margin, base.height - height - margin)
    elif position == "bottom-left":
        origin = (margin, base.height - height - margin)
    elif position == "top-right":
        origin = (base.width - width - margin, margin)
    elif position == "top-left":
        origin = (margin, margin)
    elif position == "center":
        origin = ((base.width - width) // 2, (base.height - height) // 2)
    else:
        raise ValueError("unknown watermark position")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.text(origin, text, fill=(255, 255, 255, round(255 * opacity)), font=font)
    return Image.alpha_composite(base, layer)


def orient(image: Image.Image, **_: Any) -> Image.Image:
    """Apply the EXIF orientation and drop metadata by re-decoding."""
    return ImageOps.exif_transpose(image)


def perspective(
    image: Image.Image, *, quad: str, width: int | None = None, height: int | None = None
) -> Image.Image:
    try:
        points = [float(value) for value in quad.split(",")]
    except ValueError as error:
        raise ValueError("quad must be x0,y0,x1,y1,x2,y2,x3,y3") from error
    if len(points) != 8:
        raise ValueError("quad must contain eight coordinates")
    target = (width or image.width, height or image.height)
    if target[0] < 1 or target[1] < 1:
        raise ValueError("target size must be positive")
    return image.transform(
        target, Image.Transform.QUAD, data=tuple(points), resample=Image.Resampling.BICUBIC
    )


def chroma_key(image: Image.Image, *, color: str = "#00ff00", tolerance: int = 40) -> Image.Image:
    if not 0 <= tolerance <= 255:
        raise ValueError("tolerance must be between 0 and 255")
    rgba = image.convert("RGBA")
    key = Image.new("RGB", rgba.size, _colour(color))
    difference = ImageChops.difference(rgba.convert("RGB"), key).convert("L")
    mask = difference.point(lambda value: 0 if value <= tolerance else 255)
    rgba.putalpha(mask)
    return rgba


def background_blur(image: Image.Image, *, radius: float = 8.0) -> Image.Image:
    if not 1 <= radius <= 100:
        raise ValueError("radius must be between 1 and 100")
    if "A" not in image.getbands():
        raise ValueError("background blur requires an alpha channel")
    blurred = image.convert("RGB").filter(ImageFilter.GaussianBlur(radius)).convert("RGBA")
    return Image.composite(image.convert("RGBA"), blurred, image.getchannel("A"))


def tilt_shift(image: Image.Image, *, radius: float = 6.0, band: float = 0.2) -> Image.Image:
    if not 1 <= radius <= 50 or not 0.05 <= band <= 0.5:
        raise ValueError("invalid tilt-shift parameters")
    blurred = image.filter(ImageFilter.GaussianBlur(radius))
    mask = Image.new("L", image.size, 255)
    centre = image.height / 2
    half = max(1, round(image.height * band / 2))
    draw = ImageDraw.Draw(mask)
    draw.rectangle((0, 0, image.width, max(0, centre - half - 1)), fill=0)
    draw.rectangle((0, min(image.height, centre + half), image.width, image.height), fill=0)
    mask = mask.filter(ImageFilter.GaussianBlur(max(1, round(image.height * 0.02))))
    return Image.composite(image, blurred, mask)


def oil_paint(image: Image.Image, *, colors: int = 12, size: int = 3) -> Image.Image:
    if not 2 <= colors <= 64 or size not in {3, 5}:
        raise ValueError("invalid oil paint parameters")
    smoothed = image.filter(ImageFilter.MedianFilter(size))
    return posterize(quantize(smoothed, colors=colors), levels=3)


def palette_map(
    image: Image.Image, *, palette: str = "000000,ffffff,ff0000,00ff00,0000ff", dither: bool = True
) -> Image.Image:
    entries = [entry for entry in palette.split(",") if entry]
    if not 1 <= len(entries) <= 256:
        raise ValueError("palette must contain 1 to 256 colours")
    colours = [_colour(entry) for entry in entries]
    flat = [channel for colour in colours for channel in colour]
    swatch = Image.new("P", (1, 1))
    swatch.putpalette(flat + [0] * (768 - len(flat)))
    quantized = image.convert("RGB").quantize(
        palette=swatch, dither=Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE
    )
    return quantized.convert("RGBA" if image.mode == "RGBA" else "RGB")


def film_grain(
    image: Image.Image,
    *,
    amount: float = 0.03,
    grain_size: float = 1.2,
    monochrome: bool = True,
    shadow_bias: bool = True,
    seed: int = 42,
) -> Image.Image:
    """标准物理胶片微粒模拟：基于银盐晶体团簇与照度加权，杜绝单像素数码噪点。"""
    if not 0.0 <= amount <= 0.5:
        raise ValueError("amount must be between 0.0 and 0.5")
    if amount == 0.0:
        return image.copy()

    import numpy as np

    alpha = image.getchannel("A") if "A" in image.getbands() else None
    rgb = image.convert("RGB")
    w, h = rgb.size
    rng = np.random.default_rng(seed)

    if grain_size > 1.0:
        small_w = max(1, int(w / grain_size))
        small_h = max(1, int(h / grain_size))
        if monochrome:
            noise_small = rng.normal(0.0, 1.0, (small_h, small_w)).astype(np.float32)
            noise_pil = Image.fromarray(
                ((noise_small * 0.5 + 0.5) * 255.0).clip(0, 255).astype(np.uint8), mode="L"
            )
            noise_resized = noise_pil.resize((w, h), Image.Resampling.BILINEAR)
            noise = (np.asarray(noise_resized, dtype=np.float32) / 255.0 - 0.5) * 2.0
            noise = np.stack([noise] * 3, axis=-1)
        else:
            noise_channels = []
            for _ in range(3):
                ns = rng.normal(0.0, 1.0, (small_h, small_w)).astype(np.float32)
                npil = Image.fromarray(
                    ((ns * 0.5 + 0.5) * 255.0).clip(0, 255).astype(np.uint8), mode="L"
                )
                nr = npil.resize((w, h), Image.Resampling.BILINEAR)
                noise_channels.append((np.asarray(nr, dtype=np.float32) / 255.0 - 0.5) * 2.0)
            noise = np.stack(noise_channels, axis=-1)
    else:
        if monochrome:
            ns = rng.normal(0.0, 1.0, (h, w)).astype(np.float32)
            noise = np.stack([ns] * 3, axis=-1)
        else:
            noise = rng.normal(0.0, 1.0, (h, w, 3)).astype(np.float32)

    arr = np.asarray(rgb, dtype=np.float32) / 255.0
    lum = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]

    if shadow_bias:
        weight = np.clip(1.0 - (lum / 0.85) ** 1.5, 0.0, 1.0)[..., None]
    else:
        weight = 1.0

    noisy = arr + noise * weight * (amount * 0.4)
    out_arr = np.clip(noisy * 255.0, 0.0, 255.0).astype(np.uint8)
    result = Image.fromarray(out_arr, mode="RGB")

    if alpha is not None:
        result = result.convert("RGBA")
        result.putalpha(alpha)
    return result


def de_ai_bloom(
    image: Image.Image,
    *,
    radius: int = 6,
    intensity: float = 0.12,
    threshold: int = 220,
    preserve_lines: bool = True,
) -> Image.Image:
    """高光漫反射保边微光晕：聚焦极高光强点，微量漫射光泽，防止画面大面积泛白。"""
    if radius < 1 or radius > 100:
        raise ValueError("radius must be between 1 and 100")
    if not 0.0 <= intensity <= 1.0:
        raise ValueError("intensity must be between 0.0 and 1.0")
    if not 0 <= threshold <= 255:
        raise ValueError("threshold must be between 0 and 255")
    if intensity == 0.0:
        return image.copy()
    alpha = image.getchannel("A") if "A" in image.getbands() else None
    rgb = image.convert("RGB")
    lum = rgb.convert("L")

    denom = max(1, 255 - threshold)
    mask = lum.point(lambda v: int(255 * ((v - threshold) / denom) ** 2.0) if v > threshold else 0)
    highlights = Image.composite(rgb, Image.new("RGB", rgb.size, (0, 0, 0)), mask)
    glow = highlights.filter(ImageFilter.GaussianBlur(radius))

    screened = ImageChops.screen(rgb, glow)
    blended = Image.blend(rgb, screened, intensity)

    if preserve_lines:
        line_mask = lum.point(lambda v: int((1.0 - v / 80.0) * 255) if v < 80 else 0)
        blended = Image.composite(rgb, blended, line_mask)

    if alpha is not None:
        blended = blended.convert("RGBA")
        blended.putalpha(alpha)
    return blended


def filmic_tone(
    image: Image.Image,
    *,
    lift_black: int = 3,
    compress_white: int = 246,
    saturation: float = 0.98,
    warmth: float = 0.0,
) -> Image.Image:
    """真实胶片色阶响应：高光软肩平滑滚降压制死白，微量垫起暗部，中间调严格保持自然曝光。"""
    if not 0 <= lift_black <= 50:
        raise ValueError("lift_black must be between 0 and 50")
    if not 150 <= compress_white <= 255:
        raise ValueError("compress_white must be between 150 and 255")
    if not 0.0 <= saturation <= 2.0:
        raise ValueError("saturation must be between 0.0 and 2.0")
    if not -1.0 <= warmth <= 1.0:
        raise ValueError("warmth must be between -1.0 and 1.0")

    alpha = image.getchannel("A") if "A" in image.getbands() else None
    rgb = image.convert("RGB")

    lut = []
    for i in range(256):
        x = i / 255.0
        lift = (lift_black / 255.0) * max(0.0, 1.0 - x / 0.2) ** 2
        if x > 0.75:
            excess = (x - 0.75) / 0.25
            max_comp = 1.0 - (compress_white / 255.0)
            comp = max_comp * (excess**1.6)
        else:
            comp = 0.0
        val = int(max(0, min(255, round((x + lift - comp) * 255.0))))
        lut.append(val)

    r_lut = [
        int(max(0, min(255, round(lut[i] + warmth * 5.0 * (1.0 - abs(i - 128) / 128.0)))))
        for i in range(256)
    ]
    b_lut = [
        int(max(0, min(255, round(lut[i] - warmth * 5.0 * (1.0 - abs(i - 128) / 128.0)))))
        for i in range(256)
    ]
    g_lut = lut

    r, g, b = rgb.split()
    r = r.point(r_lut)
    g = g.point(g_lut)
    b = b.point(b_lut)
    graded = Image.merge("RGB", (r, g, b))

    if saturation != 1.0:
        graded = ImageEnhance.Color(graded).enhance(saturation)

    if alpha is not None:
        graded = graded.convert("RGBA")
        graded.putalpha(alpha)
    return graded


def chromatic_aberration(image: Image.Image, *, shift: int = 2, radial: bool = True) -> Image.Image:
    """Optical chromatic dispersion: slight red/blue fringing towards frame edges."""
    if not 1 <= shift <= 30:
        raise ValueError("shift must be between 1 and 30")
    alpha = image.getchannel("A") if "A" in image.getbands() else None
    rgb = image.convert("RGB")
    w, h = image.size
    r, g, b = rgb.split()

    if radial:
        scale_r = 1.0 + (shift * 2.0) / max(w, h)
        scale_b = max(0.5, 1.0 - (shift * 2.0) / max(w, h))

        rw, rh = max(w, round(w * scale_r)), max(h, round(h * scale_r))
        r_big = r.resize((rw, rh), Image.Resampling.BILINEAR)
        r_crop = r_big.crop(((rw - w) // 2, (rh - h) // 2, (rw - w) // 2 + w, (rh - h) // 2 + h))

        bw, bh = max(1, round(w * scale_b)), max(1, round(h * scale_b))
        b_small = b.resize((bw, bh), Image.Resampling.BILINEAR)
        b_pad = Image.new("L", (w, h), 0)
        b_pad.paste(b_small, ((w - bw) // 2, (h - bh) // 2))

        result = Image.merge("RGB", (r_crop, g, b_pad))
    else:
        r_off = ImageChops.offset(r, shift, 0)
        b_off = ImageChops.offset(b, -shift, 0)
        result = Image.merge("RGB", (r_off, g, b_off))

    if alpha is not None:
        result = result.convert("RGBA")
        result.putalpha(alpha)
    return result


def vignette(
    image: Image.Image, *, radius: float = 0.75, feather: float = 0.5, darkness: float = 0.5
) -> Image.Image:
    """Lens light falloff / vignette."""
    if not 0.1 <= radius <= 2.0:
        raise ValueError("radius must be between 0.1 and 2.0")
    if not 0.05 <= feather <= 1.0:
        raise ValueError("feather must be between 0.05 and 1.0")
    if not 0.0 <= darkness <= 1.0:
        raise ValueError("darkness must be between 0.0 and 1.0")
    if darkness == 0.0:
        return image.copy()

    alpha = image.getchannel("A") if "A" in image.getbands() else None
    rgb = image.convert("RGB")
    w, h = image.size

    mw, mh = 64, 64
    mask_bytes = bytearray(mw * mh)
    inv_feather = 1.0 / feather
    for y in range(mh):
        ny = (y - mh / 2) / (mh / 2)
        for x in range(mw):
            nx = (x - mw / 2) / (mw / 2)
            dist = math.sqrt(nx * nx + ny * ny)
            if dist <= radius:
                factor = 1.0
            elif dist >= radius + feather:
                factor = 1.0 - darkness
            else:
                t = (dist - radius) * inv_feather
                smooth_t = t * t * (3.0 - 2.0 * t)
                factor = 1.0 - darkness * smooth_t
            mask_bytes[y * mw + x] = int(factor * 255)

    small_mask = Image.frombytes("L", (mw, mh), bytes(mask_bytes))
    mask = small_mask.resize((w, h), Image.Resampling.BILINEAR)

    result = ImageChops.multiply(rgb, mask.convert("RGB"))
    if alpha is not None:
        result = result.convert("RGBA")
        result.putalpha(alpha)
    return result


def color_temperature(
    image: Image.Image, *, temperature: float = 0.0, tint: float = 0.0
) -> Image.Image:
    """Warm/cool and tint color balance."""
    if not -1.0 <= temperature <= 1.0:
        raise ValueError("temperature must be between -1.0 and 1.0")
    if not -1.0 <= tint <= 1.0:
        raise ValueError("tint must be between -1.0 and 1.0")
    if temperature == 0.0 and tint == 0.0:
        return image.copy()

    alpha = image.getchannel("A") if "A" in image.getbands() else None
    rgb = image.convert("RGB")

    r_scale = max(0.0, 1.0 + 0.18 * temperature + 0.06 * tint)
    g_scale = max(0.0, 1.0 - 0.12 * tint)
    b_scale = max(0.0, 1.0 - 0.18 * temperature + 0.06 * tint)

    r_lut = [min(255, int(i * r_scale)) for i in range(256)]
    g_lut = [min(255, int(i * g_scale)) for i in range(256)]
    b_lut = [min(255, int(i * b_scale)) for i in range(256)]

    r, g, b = rgb.split()
    r = r.point(r_lut)
    g = g.point(g_lut)
    b = b.point(b_lut)
    result = Image.merge("RGB", (r, g, b))

    if alpha is not None:
        result = result.convert("RGBA")
        result.putalpha(alpha)
    return result


def focus_lines(
    image: Image.Image,
    *,
    lines: int = 120,
    center_x: float = 0.5,
    center_y: float = 0.5,
    inner_radius: float = 0.35,
    max_width: int = 3,
    line_color: str = "#000000",
    seed: int = 42,
) -> Image.Image:
    """Manga / anime dramatic radial action lines (集中线 / 速度线)."""
    if not 10 <= lines <= 600:
        raise ValueError("lines must be between 10 and 600")
    if not 0.0 <= center_x <= 1.0 or not 0.0 <= center_y <= 1.0:
        raise ValueError("center coordinates must be between 0.0 and 1.0")
    if not 0.05 <= inner_radius <= 0.95:
        raise ValueError("inner_radius must be between 0.05 and 0.95")
    if not 1 <= max_width <= 20:
        raise ValueError("max_width must be between 1 and 20")

    w, h = image.size
    cx, cy = w * center_x, h * center_y
    rx = w * inner_radius * 0.5
    ry = h * inner_radius * 0.5
    outer_diag = math.hypot(w, h) * 1.5

    col_rgb = _colour(line_color)
    col_rgba = (*col_rgb, 255)

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    rng = random.Random(seed)

    step = 2.0 * math.pi / lines
    for i in range(lines):
        angle = i * step + rng.uniform(-step * 0.35, step * 0.35)
        line_w = rng.uniform(1.0, float(max_width))
        inner_jitter = rng.uniform(0.85, 1.25)

        p_in_x = cx + rx * inner_jitter * math.cos(angle)
        p_in_y = cy + ry * inner_jitter * math.sin(angle)

        perp_angle = angle + math.pi / 2.0
        p_out_cx = cx + outer_diag * math.cos(angle)
        p_out_cy = cy + outer_diag * math.sin(angle)

        dx = math.cos(perp_angle) * line_w
        dy = math.sin(perp_angle) * line_w

        p_out1 = (p_out_cx - dx, p_out_cy - dy)
        p_out2 = (p_out_cx + dx, p_out_cy + dy)

        draw.polygon([(p_in_x, p_in_y), p_out1, p_out2], fill=col_rgba)

    base = image.convert("RGBA")
    composited = Image.alpha_composite(base, overlay)
    return composited if image.mode == "RGBA" else composited.convert("RGB")


def screentone(
    image: Image.Image, *, frequency: int = 40, angle: float = 45.0, contrast: float = 1.2
) -> Image.Image:
    """Manga screentone (黑白漫画网点纸)."""
    if not 5 <= frequency <= 150:
        raise ValueError("frequency must be between 5 and 150")
    if not 0.0 <= angle <= 90.0:
        raise ValueError("angle must be between 0.0 and 90.0")
    if not 0.1 <= contrast <= 3.0:
        raise ValueError("contrast must be between 0.1 and 3.0")

    alpha = image.getchannel("A") if "A" in image.getbands() else None
    gray = image.convert("L")
    if contrast != 1.0:
        gray = ImageEnhance.Contrast(gray).enhance(contrast)

    w, h = image.size
    period = max(2, min(w, h) // frequency)
    rad = math.radians(angle)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    inv_p = 2.0 * math.pi / period

    gray_bytes = gray.tobytes()
    out_bytes = bytearray(w * h)

    idx = 0
    for y in range(h):
        for x in range(w):
            u = x * cos_a - y * sin_a
            v = x * sin_a + y * cos_a
            dot_thresh = int(127.5 * (math.cos(u * inv_p) * math.cos(v * inv_p) + 1.0))
            lum = gray_bytes[idx]
            out_bytes[idx] = 255 if lum > dot_thresh else 0
            idx += 1

    result = Image.frombytes("L", (w, h), bytes(out_bytes)).convert("RGB")
    if alpha is not None:
        result = result.convert("RGBA")
        result.putalpha(alpha)
    return result


def sticker_outline(
    image: Image.Image,
    *,
    width: int = 6,
    color: str = "#ffffff",
    shadow: bool = True,
    shadow_offset: int = 4,
    shadow_blur: int = 4,
) -> Image.Image:
    """ACG Die-cut sticker outline with optional drop shadow."""
    if not 1 <= width <= 50:
        raise ValueError("width must be between 1 and 50")
    if not 0 <= shadow_offset <= 50:
        raise ValueError("shadow_offset must be between 0 and 50")
    if not 0 <= shadow_blur <= 50:
        raise ValueError("shadow_blur must be between 0 and 50")

    if "A" not in image.getbands():
        raise ValueError("sticker_outline requires an image with transparency (alpha channel)")

    w, h = image.size
    alpha = image.getchannel("A")
    outline_mask = alpha.filter(ImageFilter.MaxFilter(width * 2 + 1))
    outline_col = (*_colour(color), 255)
    outline_layer = Image.new("RGBA", (w, h), outline_col)

    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))

    if shadow:
        shadow_mask = outline_mask.filter(ImageFilter.GaussianBlur(shadow_blur))
        shadow_layer = Image.new("RGBA", (w, h), (0, 0, 0, 150))
        shadow_shifted = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        shadow_shifted.paste(shadow_layer, (shadow_offset, shadow_offset), shadow_mask)
        canvas = Image.alpha_composite(canvas, shadow_shifted)

    canvas.paste(outline_layer, (0, 0), outline_mask)
    canvas.paste(image, (0, 0), alpha)
    return canvas


def lineart_extract(
    image: Image.Image,
    *,
    radius: float = 1.0,
    k: float = 1.6,
    gamma: float = 0.98,
    threshold: float = 0.02,
) -> Image.Image:
    """XDoG (Extended Difference of Gaussians) clean line art extraction."""
    if not 0.2 <= radius <= 10.0:
        raise ValueError("radius must be between 0.2 and 10.0")
    if not 1.1 <= k <= 5.0:
        raise ValueError("k must be between 1.1 and 5.0")
    if not 0.5 <= gamma <= 1.0:
        raise ValueError("gamma must be between 0.5 and 1.0")
    if not 0.0 <= threshold <= 0.5:
        raise ValueError("threshold must be between 0.0 and 0.5")

    gray = image.convert("L")
    g1 = gray.filter(ImageFilter.GaussianBlur(radius))
    g2 = gray.filter(ImageFilter.GaussianBlur(radius * k))

    b1 = g1.tobytes()
    b2 = g2.tobytes()
    out = bytearray(len(b1))

    thresh_val = threshold * 255.0
    for i in range(len(b1)):
        diff = b1[i] - gamma * b2[i]
        out[i] = 0 if diff < thresh_val else 255

    return Image.frombytes("L", gray.size, bytes(out)).convert("RGB")


def aces_tonemap(
    image: Image.Image,
    *,
    exposure: float = 1.0,
    contrast: float = 1.0,
) -> Image.Image:
    """ACES (Academy Color Encoding System) filmic tone mapping curve.

    Stephen Hill & Krzysztof Narkowicz (SIGGRAPH 2015).
    Preserves cinematic highlight roll-off and shadows while eliminating plastic over-saturation.
    """
    import numpy as np

    alpha = image.getchannel("A") if "A" in image.getbands() else None
    rgb = image.convert("RGB")
    arr = np.asarray(rgb, dtype=np.float32) / 255.0
    x = arr * max(0.1, exposure)
    a, b, c, d, e = 2.51, 0.03, 2.43, 0.59, 0.14
    mapped = (x * (a * x + b)) / (x * (c * x + d) + e)
    mapped = np.clip(mapped, 0.0, 1.0)
    if contrast != 1.0:
        mapped = np.clip((mapped - 0.5) * max(0.2, contrast) + 0.5, 0.0, 1.0)
    res = Image.fromarray((mapped * 255.0).astype(np.uint8), "RGB")
    if alpha is not None:
        res = res.convert("RGBA")
        res.putalpha(alpha)
    return res


def guided_filter(
    image: Image.Image,
    *,
    radius: int = 4,
    eps: float = 0.04,
    boost: float = 1.5,
) -> Image.Image:
    """Guided image filter for edge-preserving texture and detail recovery.

    He, Sun & Tang (ECCV 2010 / TPAMI 2013).
    Restores fine hairs, skin micro-texture and fabrics without halo artifacts.
    """
    import numpy as np

    alpha = image.getchannel("A") if "A" in image.getbands() else None
    rgb = image.convert("RGB")
    r = max(1, int(radius))
    eps_val = max(1e-5, float(eps))
    boost_val = float(boost)

    gray = rgb.convert("L")
    p = np.asarray(gray, dtype=np.float32) / 255.0
    mean_p = np.asarray(gray.filter(ImageFilter.BoxBlur(r)), dtype=np.float32) / 255.0
    p2 = Image.fromarray(np.clip(p * p * 255.0, 0, 255).astype(np.uint8))
    mean_p2 = np.asarray(p2.filter(ImageFilter.BoxBlur(r)), dtype=np.float32) / 255.0
    var_p = np.maximum(0.0, mean_p2 - mean_p * mean_p)

    a_coeff = var_p / (var_p + eps_val)
    b_coeff = mean_p - a_coeff * mean_p

    img_a = Image.fromarray(np.clip(a_coeff * 255.0, 0, 255).astype(np.uint8))
    img_b = Image.fromarray(np.clip(b_coeff * 255.0, 0, 255).astype(np.uint8))
    mean_a = np.asarray(img_a.filter(ImageFilter.BoxBlur(r)), dtype=np.float32) / 255.0
    mean_b = np.asarray(img_b.filter(ImageFilter.BoxBlur(r)), dtype=np.float32) / 255.0

    base_lum = mean_a * p + mean_b
    high_freq = (p - base_lum) * boost_val

    rgb_arr = np.asarray(rgb, dtype=np.float32)
    enhanced = np.clip(rgb_arr + np.dstack([high_freq * 255.0] * 3), 0, 255).astype(np.uint8)
    res = Image.fromarray(enhanced, "RGB")
    if alpha is not None:
        res = res.convert("RGBA")
        res.putalpha(alpha)
    return res


def kuwahara(
    image: Image.Image,
    *,
    size: int = 5,
) -> Image.Image:
    """Kuwahara edge-preserving smoothing filter for anime & cel-shading painting style.

    Papari et al. (IEEE TIP 2007) / Kyprianidis (2012).
    Smooths uniform regions while preserving razor-sharp character contours and lineart.
    """
    import numpy as np

    alpha = image.getchannel("A") if "A" in image.getbands() else None
    rgb = image.convert("RGB")
    r = max(1, int(size) // 2)
    gray = rgb.convert("L")
    w, h = rgb.size
    rgb_arr = np.asarray(rgb, dtype=np.float32)

    offsets = [(-r // 2, -r // 2), (r // 2, -r // 2), (-r // 2, r // 2), (r // 2, r // 2)]
    means_rgb = []
    variances = []
    for ox, oy in offsets:
        shifted_img = gray.transform((w, h), Image.Transform.AFFINE, (1, 0, ox, 0, 1, oy))
        m_l = np.asarray(shifted_img.filter(ImageFilter.BoxBlur(r)), dtype=np.float32) / 255.0
        shifted_l2 = Image.fromarray(np.clip(m_l * m_l * 255.0, 0, 255).astype(np.uint8))
        m_l2 = np.asarray(shifted_l2.filter(ImageFilter.BoxBlur(r)), dtype=np.float32) / 255.0
        v = np.maximum(0.0, m_l2 - m_l * m_l)
        variances.append(v)

        shifted_rgb = rgb.transform((w, h), Image.Transform.AFFINE, (1, 0, ox, 0, 1, oy))
        m_rgb = np.asarray(shifted_rgb.filter(ImageFilter.BoxBlur(r)), dtype=np.float32)
        means_rgb.append(m_rgb)

    var_stack = np.stack(variances, axis=-1)
    min_idx = np.argmin(var_stack, axis=-1)
    out_arr = np.zeros_like(rgb_arr)
    for i in range(4):
        mask = (min_idx == i)[:, :, np.newaxis]
        out_arr += means_rgb[i] * mask

    res = Image.fromarray(np.clip(out_arr, 0, 255).astype(np.uint8), "RGB")
    if alpha is not None:
        res = res.convert("RGBA")
        res.putalpha(alpha)
    return res


def anamorphic_flare(
    image: Image.Image,
    *,
    threshold: int = 215,
    length: int = 36,
    tint: str = "#4a90e2",
    intensity: float = 0.65,
) -> Image.Image:
    """Anamorphic widescreen horizontal streak flare and optical diffraction.

    Keshmirian (2008). Simulates horizontal cinema flares on bright specular highlights.
    """
    import numpy as np

    alpha = image.getchannel("A") if "A" in image.getbands() else None
    rgb = image.convert("RGB")
    w, h = rgb.size
    t_val = min(254, max(50, int(threshold)))
    len_val = max(8, int(length))
    tint_rgb = _colour(tint)

    gray = rgb.convert("L")
    mask = gray.point(lambda p: p if p >= t_val else 0)
    # Anisotropic horizontal streak convolution
    streak = mask.filter(ImageFilter.BoxBlur((len_val, 1)))

    streak_arr = np.asarray(streak, dtype=np.float32) / 255.0
    streak_arr = np.minimum(1.0, streak_arr * (len_val * 0.5))
    scale = float(intensity) * 1.5
    flare_rgb = np.dstack(
        [
            streak_arr * (tint_rgb[0] * scale),
            streak_arr * (tint_rgb[1] * scale),
            streak_arr * (tint_rgb[2] * scale),
        ]
    )

    base_arr = np.asarray(rgb, dtype=np.float32)
    blended = np.clip(base_arr + flare_rgb, 0, 255).astype(np.uint8)
    res = Image.fromarray(blended, "RGB")
    if alpha is not None:
        res = res.convert("RGBA")
        res.putalpha(alpha)
    return res


def color_transfer(
    image: Image.Image,
    *,
    style: str = "anime_sky",
    strength: float = 1.0,
) -> Image.Image:
    """Statistical color transfer in Ruderman opponent/Lab space.

    Reinhard et al. (IEEE CGA 2001). Transfers filmic or anime palettes naturally.
    """
    import numpy as np

    alpha = image.getchannel("A") if "A" in image.getbands() else None
    rgb = image.convert("RGB")
    arr = np.asarray(rgb, dtype=np.float32) / 255.0

    lum_l = (arr[:, :, 0] + arr[:, :, 1] + arr[:, :, 2]) / np.sqrt(3)
    a = (arr[:, :, 0] + arr[:, :, 1] - 2 * arr[:, :, 2]) / np.sqrt(6)
    b = (arr[:, :, 0] - arr[:, :, 1]) / np.sqrt(2)

    mu_l, s_l = float(np.mean(lum_l)), float(np.std(lum_l)) + 1e-5
    mu_a, s_a = float(np.mean(a)), float(np.std(a)) + 1e-5
    mu_b, s_b = float(np.mean(b)), float(np.std(b)) + 1e-5

    presets_stats: dict[str, tuple[float, float, float, float, float, float]] = {
        "anime_sky": (0.60, 0.22, -0.05, 0.18, -0.02, 0.16),
        "cyberpunk": (0.45, 0.26, 0.12, 0.28, -0.15, 0.25),
        "vintage_warm": (0.52, 0.20, 0.04, 0.14, 0.08, 0.15),
        "morandi": (0.50, 0.14, 0.01, 0.08, 0.02, 0.09),
    }
    t_mu_l, t_s_l, t_mu_a, t_s_a, t_mu_b, t_s_b = presets_stats.get(
        style, presets_stats["anime_sky"]
    )

    alpha_ratio = min(2.0, max(0.5, t_s_l / s_l))
    new_lum_l = (lum_l - mu_l) * alpha_ratio + t_mu_l
    new_a = (a - mu_a) * min(2.0, max(0.5, t_s_a / s_a)) + t_mu_a
    new_b = (b - mu_b) * min(2.0, max(0.5, t_s_b / s_b)) + t_mu_b

    r_out = new_lum_l / np.sqrt(3) + new_a / np.sqrt(6) + new_b / np.sqrt(2)
    g_out = new_lum_l / np.sqrt(3) + new_a / np.sqrt(6) - new_b / np.sqrt(2)
    b_out = new_lum_l / np.sqrt(3) - 2 * new_a / np.sqrt(6)

    mapped_rgb = np.clip(np.dstack([r_out, g_out, b_out]), 0.0, 1.0)
    str_val = min(1.0, max(0.0, float(strength)))
    final_arr = (arr * (1.0 - str_val) + mapped_rgb * str_val) * 255.0
    res = Image.fromarray(np.clip(final_arr, 0, 255).astype(np.uint8), "RGB")
    if alpha is not None:
        res = res.convert("RGBA")
        res.putalpha(alpha)
    return res


def line_color(image: Image.Image, *, strength: float = 0.8, darkness: float = 0.5) -> Image.Image:
    """Adaptive lineart recoloring: soften harsh dark lines with ambient base colors."""
    if not 0.0 <= strength <= 1.0:
        raise ValueError("strength must be between 0.0 and 1.0")
    if not 0.0 <= darkness <= 1.0:
        raise ValueError("darkness must be between 0.0 and 1.0")
    if strength == 0.0:
        return image.copy()

    import numpy as np

    alpha = image.getchannel("A") if "A" in image.getbands() else None
    rgb = image.convert("RGB")
    w, h = rgb.size

    blur_rad = max(2, min(w, h) // 20)
    blurred = rgb.filter(ImageFilter.GaussianBlur(blur_rad))

    orig_arr = np.array(rgb, dtype=np.float32)
    base_arr = np.array(blurred, dtype=np.float32)

    lum = 0.299 * orig_arr[..., 0] + 0.587 * orig_arr[..., 1] + 0.114 * orig_arr[..., 2]
    line_weight = np.clip((140.0 - lum) / 140.0, 0.0, 1.0) * strength

    tinted_line = base_arr * (darkness * 0.6 + 0.15)
    blended = orig_arr * (1.0 - line_weight[..., None]) + tinted_line * line_weight[..., None]
    result = Image.fromarray(np.clip(blended, 0, 255).astype(np.uint8), mode="RGB")

    if alpha is not None:
        result = result.convert("RGBA")
        result.putalpha(alpha)
    return result


def color_balance(
    image: Image.Image,
    *,
    shadows: tuple[float, float, float] | list[float] = (0.0, 0.0, 0.0),
    midtones: tuple[float, float, float] | list[float] = (0.0, 0.0, 0.0),
    highlights: tuple[float, float, float] | list[float] = (0.0, 0.0, 0.0),
) -> Image.Image:
    """Three-way color balance across shadows, midtones, and highlights."""
    import numpy as np

    sh = [float(v) for v in shadows]
    md = [float(v) for v in midtones]
    hl = [float(v) for v in highlights]
    if len(sh) != 3 or len(md) != 3 or len(hl) != 3:
        raise ValueError("shadows, midtones, and highlights must each have 3 components (RGB)")
    for component_arr in (sh, md, hl):
        for v in component_arr:
            if not -1.0 <= v <= 1.0:
                raise ValueError("color balance shift values must be between -1.0 and 1.0")

    alpha = image.getchannel("A") if "A" in image.getbands() else None
    rgb = image.convert("RGB")
    arr = np.array(rgb, dtype=np.float32) / 255.0

    lum = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
    w_s = np.clip((0.5 - lum) / 0.5, 0.0, 1.0) ** 1.5
    w_h = np.clip((lum - 0.5) / 0.5, 0.0, 1.0) ** 1.5
    w_m = np.clip(1.0 - np.abs(lum - 0.5) / 0.5, 0.0, 1.0) ** 1.5

    shift_s = np.array(sh, dtype=np.float32) * 0.35
    shift_m = np.array(md, dtype=np.float32) * 0.35
    shift_h = np.array(hl, dtype=np.float32) * 0.35

    delta = w_s[..., None] * shift_s + w_m[..., None] * shift_m + w_h[..., None] * shift_h

    out_arr = np.clip((arr + delta) * 255.0, 0, 255).astype(np.uint8)
    result = Image.fromarray(out_arr, mode="RGB")
    if alpha is not None:
        result = result.convert("RGBA")
        result.putalpha(alpha)
    return result


def fisheye(image: Image.Image, *, strength: float = 0.35, zoom: float = 1.0) -> Image.Image:
    """标准广角透镜桶形畸变模拟：四角自适应归一化，视场完整，杜绝四周黑色死区。"""
    if not -0.8 <= strength <= 1.5:
        raise ValueError("strength must be between -0.8 and 1.5")
    if not 0.5 <= zoom <= 2.5:
        raise ValueError("zoom must be between 0.5 and 2.5")
    if strength == 0.0 and zoom == 1.0:
        return image.copy()

    import numpy as np

    alpha = image.getchannel("A") if "A" in image.getbands() else None
    rgb = image.convert("RGB")
    w, h = rgb.size
    arr = np.asarray(rgb, dtype=np.float32)

    y_coords, x_coords = np.mgrid[0:h, 0:w]
    cx = (w - 1) / 2.0
    cy = (h - 1) / 2.0
    max_r = np.sqrt(cx**2 + cy**2)

    norm_x = (x_coords - cx) / max_r
    norm_y = (y_coords - cy) / max_r
    r = np.sqrt(norm_x**2 + norm_y**2)

    if strength > 0:
        factor = (1.0 + strength * (r**2)) / ((1.0 + strength) * zoom)
    else:
        factor = (1.0 + strength * (r**2)) / zoom

    src_norm_x = norm_x * factor
    src_norm_y = norm_y * factor

    src_x = np.clip(src_norm_x * max_r + cx, 0.0, w - 1.0001)
    src_y = np.clip(src_norm_y * max_r + cy, 0.0, h - 1.0001)

    x0 = np.floor(src_x).astype(np.int32)
    x1 = np.minimum(x0 + 1, w - 1)
    y0 = np.floor(src_y).astype(np.int32)
    y1 = np.minimum(y0 + 1, h - 1)

    wx = (src_x - x0)[..., None]
    wy = (src_y - y0)[..., None]

    val_00 = arr[y0, x0]
    val_01 = arr[y0, x1]
    val_10 = arr[y1, x0]
    val_11 = arr[y1, x1]

    top = val_00 * (1.0 - wx) + val_01 * wx
    bottom = val_10 * (1.0 - wx) + val_11 * wx
    interpolated = top * (1.0 - wy) + bottom * wy

    out_arr = np.clip(interpolated, 0.0, 255.0).astype(np.uint8)
    result = Image.fromarray(out_arr, mode="RGB")
    if alpha is not None:
        result = result.convert("RGBA")
        result.putalpha(alpha)
    return result


def vibrance(image: Image.Image, *, amount: float = 0.3) -> Image.Image:
    """Smart saturation boosting muted tones while protecting saturated colors and skin tones."""
    if not -1.0 <= amount <= 1.0:
        raise ValueError("amount must be between -1.0 and 1.0")
    if amount == 0.0:
        return image.copy()

    import numpy as np

    alpha = image.getchannel("A") if "A" in image.getbands() else None
    rgb = image.convert("RGB")
    arr = np.array(rgb, dtype=np.float32)

    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    max_c = np.maximum(np.maximum(r, g), b)
    min_c = np.minimum(np.minimum(r, g), b)
    luma = 0.299 * r + 0.587 * g + 0.114 * b

    sat = np.where(max_c > 1e-4, (max_c - min_c) / (max_c + 1e-4), 0.0)
    gain = (1.0 - sat) * amount

    skin_mask = (r > g) & (g > b) & ((r - b) > 20)
    gain = np.where(skin_mask, gain * 0.5, gain)

    out_arr = luma[..., None] + (arr - luma[..., None]) * (1.0 + gain[..., None])
    result = Image.fromarray(np.clip(out_arr, 0, 255).astype(np.uint8), mode="RGB")

    if alpha is not None:
        result = result.convert("RGBA")
        result.putalpha(alpha)
    return result


def diffuse_glow(
    image: Image.Image,
    *,
    threshold: int = 180,
    radius: int = 20,
    intensity: float = 0.4,
) -> Image.Image:
    """Ethereal diffuse glow / bloom for anime & dreamlike highlight diffusion."""
    if not 0 <= threshold <= 255:
        raise ValueError("threshold must be between 0 and 255")
    if not 1 <= radius <= 100:
        raise ValueError("radius must be between 1 and 100")
    if not 0.0 <= intensity <= 1.0:
        raise ValueError("intensity must be between 0.0 and 1.0")
    if intensity == 0.0:
        return image.copy()

    alpha = image.getchannel("A") if "A" in image.getbands() else None
    rgb = image.convert("RGB")
    lum = rgb.convert("L")

    denom = max(1, 255 - threshold)
    mask = lum.point(lambda v: int(255 * ((v - threshold) / denom) ** 1.3) if v > threshold else 0)
    highlights = Image.composite(rgb, Image.new("RGB", rgb.size, (0, 0, 0)), mask)
    glow = highlights.filter(ImageFilter.GaussianBlur(radius))

    screened = ImageChops.screen(rgb, glow)
    blended = Image.blend(rgb, screened, intensity)

    if alpha is not None:
        blended = blended.convert("RGBA")
        blended.putalpha(alpha)
    return blended


def anti_checkerboard(
    image: Image.Image,
    *,
    strength: float = 0.5,
    cutoff: float = 0.85,
) -> Image.Image:
    """频域网格陷波与混叠抑制：消除生成模型潜空间反卷积引入的周期性棋盘格网格尖峰。"""
    if not 0.0 <= strength <= 1.0:
        raise ValueError("strength must be between 0.0 and 1.0")
    if not 0.5 <= cutoff <= 1.0:
        raise ValueError("cutoff must be between 0.5 and 1.0")
    if strength == 0.0:
        return image.copy()

    import numpy as np

    alpha = image.getchannel("A") if "A" in image.getbands() else None
    rgb = image.convert("RGB")
    arr = np.asarray(rgb, dtype=np.float32)
    h, w, c = arr.shape

    u = np.fft.fftfreq(h)
    v = np.fft.rfftfreq(w)
    u_grid, v_grid = np.meshgrid(u, v, indexing="ij")
    freq_radius = np.sqrt(u_grid**2 + v_grid**2)
    norm_radius = freq_radius / (np.sqrt(0.5**2 + 0.5**2))

    dist_u = np.abs(u_grid)
    dist_v = np.abs(v_grid)
    notch_mask = np.ones((h, len(v)), dtype=np.float32)
    high_freq_zone = norm_radius > cutoff
    axial_zone = (dist_u > 0.35) | (dist_v > 0.35)
    target_zone = high_freq_zone & axial_zone
    notch_mask[target_zone] = 1.0 - strength * 0.75

    filtered_channels = []
    for ch in range(c):
        f = np.fft.rfft2(arr[:, :, ch])
        f_filtered = f * notch_mask
        restored = np.fft.irfft2(f_filtered, s=(h, w))
        filtered_channels.append(restored)

    filtered_arr = np.stack(filtered_channels, axis=-1)
    filtered_arr = np.clip(filtered_arr, 0.0, 255.0).astype(np.uint8)
    filtered_img = Image.fromarray(filtered_arr, mode="RGB")

    blended = Image.blend(rgb, filtered_img, strength)
    if alpha is not None:
        blended = blended.convert("RGBA")
        blended.putalpha(alpha)
    return blended


def subsurface_scattering(
    image: Image.Image,
    *,
    radius: int = 4,
    scatter_strength: float = 0.15,
    skin_only: bool = True,
) -> Image.Image:
    """生物次表面散射漫反射模拟：波长依赖性光子扩散消除 AI 均一塑料/蜡质反光。"""
    if not 1 <= radius <= 30:
        raise ValueError("radius must be between 1 and 30")
    if not 0.0 <= scatter_strength <= 1.0:
        raise ValueError("scatter_strength must be between 0.0 and 1.0")
    if scatter_strength == 0.0:
        return image.copy()

    import numpy as np

    alpha = image.getchannel("A") if "A" in image.getbands() else None
    rgb = image.convert("RGB")
    r, g, b = rgb.split()

    r_diffused = r.filter(ImageFilter.GaussianBlur(radius))
    g_diffused = g.filter(ImageFilter.GaussianBlur(max(1, radius // 2)))
    b_diffused = b.filter(ImageFilter.GaussianBlur(max(1, radius // 4)))
    diffused_rgb = Image.merge("RGB", (r_diffused, g_diffused, b_diffused))

    if skin_only:
        ycbcr = rgb.convert("YCbCr")
        lum_ch, cb, cr = ycbcr.split()
        lum_arr = np.asarray(lum_ch, dtype=np.int16)
        cb_arr = np.asarray(cb, dtype=np.int16)
        cr_arr = np.asarray(cr, dtype=np.int16)
        # 排除镜面强反光区（高光不参与内部 SSS 漫射，防止冲白）
        skin_mask = (
            (cb_arr >= 77)
            & (cb_arr <= 127)
            & (cr_arr >= 133)
            & (cr_arr <= 173)
            & (lum_arr < 240)
        )
        mask_img = Image.fromarray((skin_mask * 255).astype(np.uint8), mode="L")
        mask_img = mask_img.filter(ImageFilter.GaussianBlur(radius=max(1, radius // 2)))
        scattered = Image.composite(diffused_rgb, rgb, mask_img)
    else:
        scattered = diffused_rgb

    blended = Image.blend(rgb, scattered, scatter_strength)

    if alpha is not None:
        blended = blended.convert("RGBA")
        blended.putalpha(alpha)
    return blended


def de_haloing(
    image: Image.Image,
    *,
    radius: int = 2,
    strength: float = 0.6,
    threshold: int = 25,
) -> Image.Image:
    """局部形态学反光晕过冲抑制：抑制高反差边缘由于过度锐化引入的明暗振铃光晕与白边。"""
    if not 1 <= radius <= 8:
        raise ValueError("radius must be between 1 and 8")
    if not 0.0 <= strength <= 1.0:
        raise ValueError("strength must be between 0.0 and 1.0")
    if not 5 <= threshold <= 100:
        raise ValueError("threshold must be between 5 and 100")
    if strength == 0.0:
        return image.copy()

    import numpy as np

    alpha = image.getchannel("A") if "A" in image.getbands() else None
    rgb = image.convert("RGB")
    size = radius * 2 + 1

    med = rgb.filter(ImageFilter.MedianFilter(size))

    arr_orig = np.asarray(rgb, dtype=np.float32)
    arr_med = np.asarray(med, dtype=np.float32)

    diff = arr_orig - arr_med
    overshoot_high = diff > threshold
    overshoot_low = diff < -threshold

    clamped = arr_orig.copy()
    clamped[overshoot_high] = (
        arr_med[overshoot_high] + threshold + (diff[overshoot_high] - threshold) * (1.0 - strength)
    )
    clamped[overshoot_low] = (
        arr_med[overshoot_low] - threshold + (diff[overshoot_low] + threshold) * (1.0 - strength)
    )

    result_arr = np.clip(clamped, 0.0, 255.0).astype(np.uint8)
    result_img = Image.fromarray(result_arr, mode="RGB")

    if alpha is not None:
        result_img = result_img.convert("RGBA")
        result_img.putalpha(alpha)
    return result_img


def surface_blur(
    image: Image.Image,
    *,
    radius: int = 4,
    threshold: float = 20.0,
    blend: float = 0.85,
) -> Image.Image:
    """表面保边去杂平滑算子：针对二次元插画平涂色块，消除AI杂色噪点并保留线条锐度。"""
    if radius < 1 or radius > 30:
        raise ValueError("radius must be between 1 and 30")
    if not 0.0 <= threshold <= 100.0:
        raise ValueError("threshold must be between 0.0 and 100.0")
    if not 0.0 <= blend <= 1.0:
        raise ValueError("blend must be between 0.0 and 1.0")
    if blend == 0.0:
        return image.copy()

    import numpy as np

    alpha = image.getchannel("A") if "A" in image.getbands() else None
    rgb = image.convert("RGB")
    arr = np.asarray(rgb, dtype=np.float32)

    blurred = np.asarray(rgb.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32)
    median_sz = max(3, (radius // 2) * 2 + 1)
    median_blurred = np.asarray(
        rgb.filter(ImageFilter.MedianFilter(size=median_sz)), dtype=np.float32
    )

    diff = np.linalg.norm(arr - blurred, axis=2, keepdims=True)
    weight = np.exp(-0.5 * (diff / max(1.0, threshold)) ** 2)

    smooth_target = 0.5 * blurred + 0.5 * median_blurred
    out = arr * (1.0 - weight * blend) + smooth_target * (weight * blend)
    out_arr = np.clip(out, 0.0, 255.0).astype(np.uint8)
    result = Image.fromarray(out_arr, mode="RGB")

    if alpha is not None:
        result = result.convert("RGBA")
        result.putalpha(alpha)
    return result


def color_purity(
    image: Image.Image,
    *,
    shadow_desat: float = 0.35,
    shadow_threshold: float = 70.0,
    vibrance_boost: float = 0.15,
) -> Image.Image:
    """动漫色彩提纯与阴影去脏：消除AI在暗部混入的杂乱高饱和色，提升二次元通透感。"""
    if not 0.0 <= shadow_desat <= 1.0:
        raise ValueError("shadow_desat must be between 0.0 and 1.0")
    if not 0.0 <= shadow_threshold <= 255.0:
        raise ValueError("shadow_threshold must be between 0.0 and 255.0")
    if not -1.0 <= vibrance_boost <= 1.0:
        raise ValueError("vibrance_boost must be between -1.0 and 1.0")

    import numpy as np

    alpha = image.getchannel("A") if "A" in image.getbands() else None
    rgb = image.convert("RGB")
    arr = np.asarray(rgb, dtype=np.float32)

    lum = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
    gray = np.stack([lum] * 3, axis=-1)

    shadow_mask = np.clip(1.0 - lum / max(1.0, shadow_threshold), 0.0, 1.0)[..., None]
    desaturated = arr * (1.0 - shadow_mask * shadow_desat) + gray * (shadow_mask * shadow_desat)

    if vibrance_boost != 0.0:
        max_c = np.max(desaturated, axis=-1, keepdims=True)
        min_c = np.min(desaturated, axis=-1, keepdims=True)
        sat = (max_c - min_c) / np.maximum(max_c, 1e-4)
        vibrance_factor = (1.0 - sat) * vibrance_boost * (1.0 - shadow_mask)
        desaturated = desaturated + (desaturated - gray) * vibrance_factor

    out_arr = np.clip(desaturated, 0.0, 255.0).astype(np.uint8)
    result = Image.fromarray(out_arr, mode="RGB")

    if alpha is not None:
        result = result.convert("RGBA")
        result.putalpha(alpha)
    return result


def anime_line_enhance(
    image: Image.Image,
    *,
    strength: float = 1.0,
    thickness: float = 1.0,
    sigma: float = 1.0,
    k_ratio: float = 1.6,
    tau: float = 0.98,
    gamma: float = 1.5,
    closing_radius: int = 1,
) -> Image.Image:
    """二次元动漫连贯线条增强与描墨算子。

    基于流向差分高斯（Flow-based Difference-of-Gaussians, FDoG）理论与形态学定向闭合，
    提取插画暗部轮廓骨架，消除AI断线与半透明虚灰，压深线条墨色并保真平涂色块。
    """
    if not 0.0 <= strength <= 3.0:
        raise ValueError("strength must be between 0.0 and 3.0")
    if not 0.2 <= thickness <= 5.0:
        raise ValueError("thickness must be between 0.2 and 5.0")
    if not 0.1 <= sigma <= 10.0:
        raise ValueError("sigma must be between 0.1 and 10.0")
    if not 1.0 <= k_ratio <= 5.0:
        raise ValueError("k_ratio must be between 1.0 and 5.0")
    if not 0.5 <= tau <= 1.2:
        raise ValueError("tau must be between 0.5 and 1.2")
    if not 0.2 <= gamma <= 5.0:
        raise ValueError("gamma must be between 0.2 and 5.0")
    if not 0 <= closing_radius <= 5:
        raise ValueError("closing_radius must be between 0 and 5")
    if strength == 0.0:
        return image.copy()

    import numpy as np

    alpha = image.getchannel("A") if "A" in image.getbands() else None
    rgb = image.convert("RGB")
    arr = np.asarray(rgb, dtype=np.float32)

    # 1. 提取亮度通道 L
    lum = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
    lum_img = Image.fromarray(np.clip(lum, 0.0, 255.0).astype(np.uint8), mode="L")

    # 2. 计算多尺度差分高斯 (Difference-of-Gaussians)
    s1 = float(sigma * thickness)
    s2 = float(s1 * k_ratio)
    g1 = np.asarray(lum_img.filter(ImageFilter.GaussianBlur(s1)), dtype=np.float32)
    g2 = np.asarray(lum_img.filter(ImageFilter.GaussianBlur(s2)), dtype=np.float32)

    # 暗色边缘差分响应
    edge_resp = np.maximum(0.0, tau * g2 - g1)

    # 3. 形态学闭合（Coherent Closing），连接 AI 生成的断裂微隙
    if closing_radius > 0:
        k_size = closing_radius * 2 + 1
        edge_img = Image.fromarray(np.clip(edge_resp * 4.0, 0.0, 255.0).astype(np.uint8), mode="L")
        closed_img = (
            edge_img.filter(ImageFilter.MaxFilter(k_size)).filter(ImageFilter.MinFilter(k_size))
        )
        edge_resp = np.asarray(closed_img, dtype=np.float32) / 4.0

    # 4. 描墨非线性对比度映射与压暗掩模
    max_val = float(np.percentile(edge_resp, 99.5))
    if max_val < 1e-4:
        return image.copy()

    norm_edge = np.clip(edge_resp / max_val, 0.0, 1.0)
    ink_mask = np.power(norm_edge, 1.0 / max(0.1, gamma))

    # 5. 保边正片叠底压墨融合 (Selective Multiply Inking)
    darken_factor = 1.0 - np.clip(ink_mask * (strength * 0.7), 0.0, 0.95)[..., None]
    out = arr * darken_factor

    out_arr = np.clip(out, 0.0, 255.0).astype(np.uint8)
    result = Image.fromarray(out_arr, mode="RGB")
    if alpha is not None:
        result = result.convert("RGBA")
        result.putalpha(alpha)
    return result


OPS: dict[str, Callable[..., Image.Image]] = {
    "resize": resize,
    "crop": crop,
    "trim": trim,
    "pad": pad,
    "seamless": seamless,
    "orient": orient,
    "perspective": perspective,
    "chroma_key": chroma_key,
    "background_blur": background_blur,
    "tilt_shift": tilt_shift,
    "oil_paint": oil_paint,
    "palette_map": palette_map,
    "pixelate": pixelate,
    "mosaic": mosaic,
    "block_scale": block_scale,
    "scale2x": scale2x,
    "quantize": quantize,
    "posterize": posterize,
    "grayscale": grayscale,
    "autocontrast": autocontrast,
    "equalize": equalize,
    "invert": invert,
    "brightness": brightness,
    "contrast": contrast,
    "saturation": saturation,
    "gamma": gamma,
    "exposure": exposure,
    "levels": levels,
    "shadows_highlights": shadows_highlights,
    "white_balance": white_balance,
    "sepia": sepia,
    "duotone": duotone,
    "blur": blur,
    "box_blur": box_blur,
    "motion_blur": motion_blur,
    "median": median,
    "minimum": minimum,
    "maximum": maximum,
    "mode_filter": mode_filter,
    "highpass": highpass,
    "sharpen": sharpen,
    "unsharp": unsharp,
    "edge": edge,
    "rotate": rotate,
    "flip": flip,
    "transpose": transpose,
    "dither": dither,
    "halftone": halftone,
    "cel_shade": cel_shade,
    "ascii": ascii_art,
    "glitch": glitch,
    "scanlines": scanlines,
    "watermark": watermark,
    "film_grain": film_grain,
    "de_ai_bloom": de_ai_bloom,
    "filmic_tone": filmic_tone,
    "chromatic_aberration": chromatic_aberration,
    "vignette": vignette,
    "color_temperature": color_temperature,
    "focus_lines": focus_lines,
    "screentone": screentone,
    "sticker_outline": sticker_outline,
    "lineart_extract": lineart_extract,
    "aces_tonemap": aces_tonemap,
    "guided_filter": guided_filter,
    "kuwahara": kuwahara,
    "anamorphic_flare": anamorphic_flare,
    "color_transfer": color_transfer,
    "line_color": line_color,
    "color_balance": color_balance,
    "fisheye": fisheye,
    "vibrance": vibrance,
    "diffuse_glow": diffuse_glow,
    "anti_checkerboard": anti_checkerboard,
    "subsurface_scattering": subsurface_scattering,
    "de_haloing": de_haloing,
    "surface_blur": surface_blur,
    "color_purity": color_purity,
    "anime_line_enhance": anime_line_enhance,
}


def _optional_ops() -> dict[str, Callable[..., Image.Image]]:
    import importlib

    operations: dict[str, Callable[..., Image.Image]] = {}
    for module in ("filters_numpy", "superres"):
        try:
            operations.update(importlib.import_module(f"imagen_cli.application.{module}").OPS)
        except ImportError:  # the optional extra is not installed
            continue
    return operations


OPS.update(_optional_ops())

FORMATS = {"png": "PNG", "jpeg": "JPEG", "jpg": "JPEG", "webp": "WEBP", "avif": "AVIF"}


def encode(
    image: Image.Image,
    *,
    format: str = "png",
    quality: int = 90,
    progressive: bool = False,
    optimize: bool = True,
    lossless: bool = False,
    target_bytes: int | None = None,
) -> bytes:
    name = FORMATS.get(format.lower())
    if name is None:
        raise ValueError("unknown output format")
    if not 1 <= quality <= 100:
        raise ValueError("quality must be between 1 and 100")
    if target_bytes is not None and target_bytes < 1:
        raise ValueError("target bytes must be positive")
    source = image.convert("RGB") if name in {"JPEG", "AVIF"} else image
    options: dict[str, Any] = {}
    if name == "PNG":
        options = {"optimize": optimize, "compress_level": 6}
        if target_bytes is not None:
            raise ValueError("target byte size requires a lossy format")
    elif name == "JPEG":
        options = {
            "quality": quality,
            "progressive": progressive,
            "subsampling": 0 if quality >= 90 else 2,
        }
    elif name == "WEBP":
        options = {"quality": 100 if lossless else quality, "lossless": lossless, "method": 4}
    else:
        options = {"quality": quality}
    if target_bytes is None:
        return _save(source, name, options)
    if name == "PNG":
        raise ValueError("target byte size requires a lossy format")
    low, high = 10, quality
    best = _save(source, name, {**options, "quality": low})
    if len(best) > target_bytes:
        raise ValueError("target byte size is unreachable")
    while low <= high:
        middle = (low + high) // 2
        candidate = _save(source, name, {**options, "quality": middle})
        if len(candidate) <= target_bytes:
            best, low = candidate, middle + 1
        else:
            high = middle - 1
    return best


def _save(image: Image.Image, name: str, options: dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    try:
        image.save(buffer, format=name, **options)
    except (KeyError, OSError) as error:
        raise ValueError(f"output format {name} is unavailable") from error
    return buffer.getvalue()
