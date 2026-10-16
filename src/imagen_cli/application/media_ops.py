"""Deterministic local image operators; no network, no provider calls."""

from __future__ import annotations

import io
from collections.abc import Callable
from typing import Any, cast

from PIL import Image, ImageFilter, ImageOps

BAYER = [
    [0, 8, 2, 10],
    [12, 4, 14, 6],
    [3, 11, 1, 9],
    [15, 7, 13, 5],
]


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


def pixelate(image: Image.Image, *, scale: int = 4) -> Image.Image:
    if scale < 1:
        raise ValueError("scale must be positive")
    small = (max(1, image.width // scale), max(1, image.height // scale))
    return image.resize(small, Image.Resampling.BOX).resize(image.size, Image.Resampling.NEAREST)


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


def invert(image: Image.Image, **_: Any) -> Image.Image:
    return ImageOps.invert(image.convert("RGB"))


def blur(image: Image.Image, *, radius: float = 2.0) -> Image.Image:
    if not 0 <= radius <= 100:
        raise ValueError("radius must be between 0 and 100")
    return image.filter(ImageFilter.GaussianBlur(radius))


def sharpen(image: Image.Image, *, radius: float = 2.0, percent: int = 150,
            threshold: int = 3) -> Image.Image:
    if not 0 <= radius <= 100 or not 0 <= percent <= 1000 or not 0 <= threshold <= 255:
        raise ValueError("invalid sharpen parameters")
    return image.filter(ImageFilter.UnsharpMask(radius, percent, threshold))


def edge(image: Image.Image, *, mode: str = "find") -> Image.Image:
    if mode == "find":
        return image.convert("RGB").filter(ImageFilter.FIND_EDGES)
    if mode == "contour":
        return image.convert("RGB").filter(ImageFilter.CONTOUR)
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


def dither(image: Image.Image, *, mode: str = "fs", colors: int = 16,
           levels: int = 2) -> Image.Image:
    if not 2 <= levels <= 8:
        raise ValueError("levels must be between 1 and 8")
    if mode not in {"fs", "bayer"}:
        raise ValueError("unknown dither mode")
    source = image.convert("L")
    width, height = source.size
    values = [[cast(int, source.getpixel((x, y))) for x in range(width)] for y in range(height)]
    step = 255 // (levels - 1)
    for y in range(height):
        for x in range(width):
            old = values[y][x]
            new = min(255, round(old / step) * step)
            values[y][x] = new
            if mode == "bayer":
                threshold = (BAYER[y % 4][x % 4] / 16 - 0.5) * step
                values[y][x] = min(255, max(0, round(new + threshold)))
                continue
            error = old - new
            for dx, dy, factor in ((1, 0, 7 / 16), (-1, 1, 3 / 16), (0, 1, 5 / 16), (1, 1, 1 / 16)):
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


OPS: dict[str, Callable[..., Image.Image]] = {
    "resize": resize, "pixelate": pixelate, "quantize": quantize, "posterize": posterize,
    "grayscale": grayscale, "autocontrast": autocontrast, "invert": invert, "blur": blur,
    "sharpen": sharpen, "edge": edge, "rotate": rotate, "flip": flip, "dither": dither,
}


def encode(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False, compress_level=6)
    return buffer.getvalue()
