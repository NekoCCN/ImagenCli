"""Deterministic local image operators; no network, no provider calls."""

from __future__ import annotations

import io
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
    "atkinson": [(1, 0, 1 / 8), (2, 0, 1 / 8), (-1, 1, 1 / 8), (0, 1, 1 / 8),
                 (1, 1, 1 / 8), (0, 2, 1 / 8)],
    "jarvis": [(1, 0, 7 / 48), (2, 0, 5 / 48), (-2, 1, 3 / 48), (-1, 1, 5 / 48),
               (0, 1, 7 / 48), (1, 1, 5 / 48), (2, 1, 3 / 48), (-2, 2, 1 / 48),
               (-1, 2, 3 / 48), (0, 2, 5 / 48), (1, 2, 3 / 48), (2, 2, 1 / 48)],
    "stucki": [(1, 0, 8 / 42), (2, 0, 4 / 42), (-2, 1, 2 / 42), (-1, 1, 4 / 42),
               (0, 1, 8 / 42), (1, 1, 4 / 42), (2, 1, 2 / 42), (-2, 2, 1 / 42),
               (-1, 2, 2 / 42), (0, 2, 4 / 42), (1, 2, 2 / 42), (2, 2, 1 / 42)],
    "burkes": [(1, 0, 8 / 32), (2, 0, 4 / 32), (-2, 1, 2 / 32), (-1, 1, 4 / 32),
               (0, 1, 8 / 32), (1, 1, 4 / 32), (2, 1, 2 / 32)],
    "sierra": [(1, 0, 5 / 32), (2, 0, 3 / 32), (-2, 1, 2 / 32), (-1, 1, 4 / 32),
               (0, 1, 5 / 32), (1, 1, 4 / 32), (2, 1, 2 / 32), (-1, 2, 2 / 32),
               (0, 2, 3 / 32), (1, 2, 2 / 32)],
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


def resize(image: Image.Image, *, width: int | None = None, height: int | None = None,
           scale: int | None = None, filter: str = "lanczos") -> Image.Image:
    filters = {
        "nearest": Image.Resampling.NEAREST, "box": Image.Resampling.BOX,
        "bilinear": Image.Resampling.BILINEAR, "bicubic": Image.Resampling.BICUBIC,
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


def crop(image: Image.Image, *, left: int = 0, top: int = 0,
         right: int | None = None, bottom: int | None = None) -> Image.Image:
    box = (left, top, image.width if right is None else right,
           image.height if bottom is None else bottom)
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
        mask = ImageChops.difference(rgb, background).convert("L").point(
            lambda value: 255 if value > threshold else 0
        )
        box = mask.getbbox()
    if box is None:
        raise ValueError("image is uniformly one colour")
    return image.crop(box)


def pad(image: Image.Image, *, width: int | None = None, height: int | None = None,
        color: str = "0,0,0") -> Image.Image:
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
    return image.resize(
        (image.width * factor, image.height * factor), Image.Resampling.NEAREST
    )


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


def quantize(image: Image.Image, *, colors: int = 16, method: str = "median_cut",
             dither: bool = False) -> Image.Image:
    if not 2 <= colors <= 256:
        raise ValueError("colors must be between 2 and 256")
    if method not in {"median_cut", "octree"}:
        raise ValueError("unknown quantization method")
    dither_method = Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE
    converted = image.convert("RGB").quantize(
        colors=colors, method=Image.Quantize.MEDIANCUT if method == "median_cut"
        else Image.Quantize.FASTOCTREE, dither=dither_method,
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
    return brightness(image, factor=2.0 ** stops)


def levels(image: Image.Image, *, black: int = 0, white: int = 255,
           midtone: float = 1.0) -> Image.Image:
    if not 0 <= black < white <= 255 or not 0.1 <= midtone <= 5:
        raise ValueError("invalid levels")
    lookup = []
    for index in range(256):
        value = (index - black) / (white - black)
        value = min(1.0, max(0.0, value)) ** (1 / midtone)
        lookup.append(round(value * 255))
    return image.convert("RGB").point(lookup * 3)


def shadows_highlights(image: Image.Image, *, shadows: float = 0.0,
                       highlights: float = 0.0) -> Image.Image:
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
        maxima = [max(index for index, count in enumerate(channel.histogram()) if count)
                  for channel in channels]
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


def unsharp(image: Image.Image, *, radius: float = 2.0, percent: int = 150,
            threshold: int = 3) -> Image.Image:
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


def dither(image: Image.Image, *, mode: str = "fs", colors: int = 16,
           levels: int = 2, size: int = 4, seed: int = 0) -> Image.Image:
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
    edges = image.convert("L").filter(ImageFilter.FIND_EDGES).point(
        lambda value: 0 if value > 24 else 255
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


def watermark(image: Image.Image, *, text: str = "imagen", size: int = 32,
              position: str = "bottom-right", opacity: float = 0.6) -> Image.Image:
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


def perspective(image: Image.Image, *, quad: str,
                width: int | None = None, height: int | None = None) -> Image.Image:
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


def palette_map(image: Image.Image, *, palette: str = "000000,ffffff,ff0000,00ff00,0000ff",
                dither: bool = True) -> Image.Image:
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


OPS: dict[str, Callable[..., Image.Image]] = {
    "resize": resize, "crop": crop, "trim": trim, "pad": pad, "seamless": seamless,
    "orient": orient, "perspective": perspective, "chroma_key": chroma_key,
    "background_blur": background_blur, "tilt_shift": tilt_shift, "oil_paint": oil_paint,
    "palette_map": palette_map,
    "pixelate": pixelate, "mosaic": mosaic, "block_scale": block_scale, "scale2x": scale2x,
    "quantize": quantize, "posterize": posterize, "grayscale": grayscale,
    "autocontrast": autocontrast, "equalize": equalize, "invert": invert,
    "brightness": brightness, "contrast": contrast, "saturation": saturation,
    "gamma": gamma, "exposure": exposure, "levels": levels,
    "shadows_highlights": shadows_highlights, "white_balance": white_balance,
    "sepia": sepia, "duotone": duotone, "blur": blur, "box_blur": box_blur,
    "motion_blur": motion_blur, "median": median, "minimum": minimum, "maximum": maximum,
    "mode_filter": mode_filter, "highpass": highpass, "sharpen": sharpen, "unsharp": unsharp,
    "edge": edge, "rotate": rotate, "flip": flip, "transpose": transpose, "dither": dither,
    "halftone": halftone, "cel_shade": cel_shade, "ascii": ascii_art, "glitch": glitch,
    "scanlines": scanlines, "watermark": watermark,
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


def encode(image: Image.Image, *, format: str = "png", quality: int = 90,
           progressive: bool = False, optimize: bool = True,
           lossless: bool = False, target_bytes: int | None = None) -> bytes:
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
        options = {"quality": quality, "progressive": progressive,
                   "subsampling": 0 if quality >= 90 else 2}
    elif name == "WEBP":
        options = {"quality": 100 if lossless else quality, "lossless": lossless,
                   "method": 4}
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
