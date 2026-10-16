"""Optional numpy-backed filters: edge preserving, LUT and signal metrics."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

import numpy as np
from PIL import Image

Array = np.ndarray


def _rgb(image: Image.Image) -> Array:
    return np.asarray(image.convert("RGB"), dtype=np.float64)


def _restore(array: Array, original: Image.Image) -> Image.Image:
    clipped = np.clip(np.round(array), 0, 255).astype(np.uint8)
    result = Image.fromarray(clipped, "RGB")
    if "A" in original.getbands():
        result = result.convert("RGBA")
        result.putalpha(original.getchannel("A"))
    return result


def _integral(array: Array) -> Array:
    height, width = array.shape[:2]
    padded = np.zeros((height + 1, width + 1, *array.shape[2:]), dtype=np.float64)
    padded[1:, 1:] = array
    return cast(Array, padded.cumsum(axis=0).cumsum(axis=1))


def _window(integral: Array, y0: Array, y1: Array, x0: Array, x1: Array) -> Array:
    return cast(
        Array, integral[y1, x1] - integral[y0, x1] - integral[y1, x0] + integral[y0, x0]
    )


def _quadrants(integral: Array, half: int) -> list[tuple[Array, Array]]:
    height, width = integral.shape[0] - 1, integral.shape[1] - 1
    ys, xs = np.mgrid[0:height, 0:width]
    boxes = []
    for dy in (0, -half):
        for dx in (0, -half):
            y0 = np.clip(ys + dy, 0, height)
            y1 = np.clip(ys + dy + half + 1, 0, height)
            x0 = np.clip(xs + dx, 0, width)
            x1 = np.clip(xs + dx + half + 1, 0, width)
            count = np.maximum(1, (y1 - y0) * (x1 - x0))[..., None]
            boxes.append((_window(integral, y0, y1, x0, x1) / count, count))
    return boxes


def kuwahara(image: Image.Image, *, size: int = 5, variant: str = "classic") -> Image.Image:
    if size not in {3, 5, 7, 9}:
        raise ValueError("kuwahara size must be 3, 5, 7 or 9")
    if variant not in {"classic", "generalized"}:
        raise ValueError("unknown kuwahara variant")
    array = _rgb(image)
    half = size // 2
    integral = _integral(array)
    squares = _integral(array * array)
    best_variance: Array | None = None
    output = array.copy()
    for (mean, _count), (mean_sq, _) in zip(
        _quadrants(integral, half), _quadrants(squares, half), strict=True
    ):
        variance = np.maximum(0.0, mean_sq - mean * mean)
        score = variance.mean(axis=2, keepdims=True)
        if best_variance is None:
            best_variance = score
            output = mean
        else:
            take = score < best_variance
            output = np.where(take, mean, output)
            best_variance = np.where(take, score, best_variance)
    if variant == "generalized":
        output = 0.5 * output + 0.5 * array
    return _restore(output, image)


def bilateral(image: Image.Image, *, radius: int = 3, sigma_color: float = 30.0,
              sigma_space: float = 3.0) -> Image.Image:
    if not 1 <= radius <= 8 or sigma_color <= 0 or sigma_space <= 0:
        raise ValueError("invalid bilateral parameters")
    array = _rgb(image)
    height, width = array.shape[:2]
    padded = np.pad(array, ((radius, radius), (radius, radius), (0, 0)), mode="edge")
    total = np.zeros_like(array)
    weight = np.zeros((height, width, 1))
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            shifted = padded[radius + dy:radius + dy + height,
                             radius + dx:radius + dx + width]
            spatial = np.exp(-(dx * dx + dy * dy) / (2 * sigma_space ** 2))
            colour = np.exp(
                -((shifted - array) ** 2).sum(axis=2, keepdims=True)
                / (2 * sigma_color ** 2)
            )
            current = spatial * colour
            total += shifted * current
            weight += current
    return _restore(total / weight, image)


def guided(image: Image.Image, *, radius: int = 4, epsilon: float = 0.01) -> Image.Image:
    if not 1 <= radius <= 16 or not 0 < epsilon <= 1:
        raise ValueError("invalid guided filter parameters")
    array = _rgb(image)
    guide = array.mean(axis=2)
    height, width = guide.shape
    padded = np.pad(guide, radius, mode="edge")
    padded_rgb = np.pad(array, ((radius, radius), (radius, radius), (0, 0)), mode="edge")
    count = (2 * radius + 1) ** 2
    sum_guide = np.zeros_like(guide)
    sum_rgb = np.zeros_like(array)
    sum_sq = np.zeros_like(guide)
    sum_cross = np.zeros_like(array)
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            window = padded[radius + dy:radius + dy + height, radius + dx:radius + dx + width]
            rgb = padded_rgb[radius + dy:radius + dy + height,
                            radius + dx:radius + dx + width]
            sum_guide += window
            sum_rgb += rgb
            sum_sq += window * window
            sum_cross += window[..., None] * rgb
    mean_guide = sum_guide / count
    mean_rgb = sum_rgb / count
    var_guide = sum_sq / count - mean_guide ** 2
    cov = sum_cross / count - mean_guide[..., None] * mean_rgb
    a = cov / (var_guide[..., None] + epsilon)
    b = mean_rgb - a * mean_guide[..., None]
    padded_a = np.pad(a, ((radius, radius), (radius, radius), (0, 0)), mode="edge")
    padded_b = np.pad(b, ((radius, radius), (radius, radius), (0, 0)), mode="edge")
    mean_a = np.zeros_like(a)
    mean_b = np.zeros_like(b)
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            mean_a += padded_a[radius + dy:radius + dy + height,
                               radius + dx:radius + dx + width]
            mean_b += padded_b[radius + dy:radius + dy + height,
                               radius + dx:radius + dx + width]
    return _restore(mean_a / count * guide[..., None] + mean_b / count, image)


def nl_means(image: Image.Image, *, strength: float = 10.0, patch: int = 2,
             search: int = 3) -> Image.Image:
    if not 0.1 <= strength <= 100 or not 1 <= patch <= 3 or not 1 <= search <= 5:
        raise ValueError("invalid non-local means parameters")
    array = _rgb(image)
    height, width = array.shape[:2]
    padded = np.pad(array, ((search + patch,) * 2, (search + patch,) * 2, (0, 0)),
                    mode="reflect")
    margin = search + patch
    total = np.zeros_like(array)
    weight = np.zeros((height, width, 1))
    for dy in range(-search, search + 1):
        for dx in range(-search, search + 1):
            shifted = padded[margin + dy:margin + dy + height,
                             margin + dx:margin + dx + width]
            if dy == 0 and dx == 0:
                distance = np.zeros_like(weight)
            else:
                distance = np.zeros_like(weight)
                for py in range(-patch, patch + 1):
                    for px in range(-patch, patch + 1):
                        first = padded[margin + dy + py:margin + dy + py + height,
                                       margin + dx + px:margin + dx + px + width]
                        second = padded[margin + py:margin + py + height,
                                        margin + px:margin + px + width]
                        distance += ((first - second) ** 2).sum(axis=2, keepdims=True)
                distance /= (2 * patch + 1) ** 2
            current = np.exp(-distance / (strength ** 2))
            total += shifted * current
            weight += current
    return _restore(total / weight, image)


def total_variation(image: Image.Image, *, weight: float = 0.1,
                    iterations: int = 20) -> Image.Image:
    if not 0 < weight <= 1 or not 1 <= iterations <= 200:
        raise ValueError("invalid total variation parameters")
    array = _rgb(image)
    result = array.copy()
    for _ in range(iterations):
        difference = result - array
        gradient = np.zeros_like(result)
        gradient[:, :-1] += np.sign(result[:, :-1] - result[:, 1:])
        gradient[:, 1:] -= np.sign(result[:, :-1] - result[:, 1:])
        gradient[:-1, :] += np.sign(result[:-1, :] - result[1:, :])
        gradient[1:, :] -= np.sign(result[:-1, :] - result[1:, :])
        result -= weight * (difference - gradient)
    return _restore(result, image)


def clahe(image: Image.Image, *, tiles: int = 8, clip: float = 2.0) -> Image.Image:
    if not 2 <= tiles <= 16 or not 1 <= clip <= 10:
        raise ValueError("invalid CLAHE parameters")
    luminance = np.asarray(image.convert("L"), dtype=np.float64)
    height, width = luminance.shape
    tile_height = max(1, height // tiles)
    tile_width = max(1, width // tiles)
    mapped = np.zeros_like(luminance)
    for y in range(0, height, tile_height):
        for x in range(0, width, tile_width):
            block = luminance[y:y + tile_height, x:x + tile_width]
            histogram, _ = np.histogram(block, bins=256, range=(0, 256))
            limit = max(1, int(clip * block.size / 256))
            excess = np.maximum(0, histogram - limit).sum()
            histogram = np.minimum(histogram, limit)
            histogram += excess // 256
            cdf = histogram.cumsum()
            if cdf[-1] == 0:
                mapping = np.arange(256)
            else:
                mapping = np.round(cdf * 255 / cdf[-1])
            mapped[y:y + tile_height, x:x + tile_width] = mapping[
                block.astype(np.uint8)
            ]
    ratio = np.where(luminance > 1, mapped / np.maximum(luminance, 1), 1.0)
    array = _rgb(image) * ratio[..., None]
    return _restore(array, image)


def curves(image: Image.Image, *, points: str = "0,0;255,255") -> Image.Image:
    try:
        control = sorted(
            tuple(int(value) for value in pair.split(",")) for pair in points.split(";")
        )
    except ValueError as error:
        raise ValueError("curves points must be x,y;x,y") from error
    if len(control) < 2 or control[0][0] != 0 or control[-1][0] != 255:
        raise ValueError("curves need points from 0 to 255")
    xs = np.array([point[0] for point in control], dtype=np.float64)
    ys = np.array([point[1] for point in control], dtype=np.float64)
    if np.any(np.diff(xs) <= 0) or np.any((ys < 0) | (ys > 255)):
        raise ValueError("curves points must be increasing and bounded")
    lookup = np.interp(np.arange(256), xs, ys)
    array = _rgb(image)
    return _restore(np.take(lookup, array.astype(np.uint8)), image)


def cube_lut(image: Image.Image, *, path: str, strength: float = 1.0) -> Image.Image:
    if not 0 <= strength <= 1:
        raise ValueError("strength must be between 0 and 1")
    source = Path(path)
    if source.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("LUT file is too large")
    size = 0
    values: list[list[float]] = []
    for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if text.upper().startswith("LUT_3D_SIZE"):
            size = int(text.split()[-1])
            continue
        if text[0].isdigit() or text[0] in "+-.":
            parts = text.split()
            if len(parts) == 3:
                values.append([float(part) for part in parts])
    if not 2 <= size <= 128 or len(values) != size ** 3:
        raise ValueError("invalid .cube LUT")
    # .cube files list red fastest, then green, then blue.
    table = np.array(values, dtype=np.float64).reshape(size, size, size, 3) * 255
    array = _rgb(image) / 255 * (size - 1)
    low = np.clip(np.floor(array).astype(int), 0, size - 2)
    high = low + 1
    fraction = array - low

    def sample(red: Array, green: Array, blue: Array) -> Array:
        return cast(Array, table[blue, green, red])

    red_low, green_low, blue_low = low[..., 0], low[..., 1], low[..., 2]
    red_high, green_high, blue_high = high[..., 0], high[..., 1], high[..., 2]
    fr, fg, fb = fraction[..., 0:1], fraction[..., 1:2], fraction[..., 2:3]
    c00 = sample(red_low, green_low, blue_low) * (1 - fr) + sample(
        red_high, green_low, blue_low) * fr
    c01 = sample(red_low, green_low, blue_high) * (1 - fr) + sample(
        red_high, green_low, blue_high) * fr
    c10 = sample(red_low, green_high, blue_low) * (1 - fr) + sample(
        red_high, green_high, blue_low) * fr
    c11 = sample(red_low, green_high, blue_high) * (1 - fr) + sample(
        red_high, green_high, blue_high) * fr
    c0 = c00 * (1 - fg) + c10 * fg
    c1 = c01 * (1 - fg) + c11 * fg
    mapped = c0 * (1 - fb) + c1 * fb
    return _restore(_rgb(image) * (1 - strength) + mapped * strength, image)


def _dark_channel(array: Array, size: int = 15) -> Array:
    half = size // 2
    minimum = array.min(axis=2, keepdims=True)
    horizontal = np.pad(minimum, ((0, 0), (half, half), (0, 0)), mode="edge")
    result = np.full_like(minimum, 255.0)
    for shift in range(size):
        result = np.minimum(result, horizontal[:, shift:shift + minimum.shape[1]])
    vertical = np.pad(result, ((half, half), (0, 0), (0, 0)), mode="edge")
    final = np.full_like(minimum, 255.0)
    for shift in range(size):
        final = np.minimum(final, vertical[shift:shift + minimum.shape[0]])
    return final


def dehaze(image: Image.Image, *, strength: float = 0.9, radius: int = 15) -> Image.Image:
    if not 0 <= strength <= 1 or not 3 <= radius <= 31 or radius % 2 == 0:
        raise ValueError("invalid dehaze parameters")
    array = _rgb(image) / 255
    dark = _dark_channel(array, radius)
    count = max(1, dark.size // 1000)
    flat = dark.reshape(-1)
    brightest = np.argsort(flat)[-count:]
    atmosphere = array.reshape(-1, 3)[brightest].mean(axis=0)
    transmission = 1 - strength * _dark_channel(array / np.maximum(atmosphere, 1e-3), radius)
    transmission = np.clip(transmission, 0.1, 1.0)
    result = (array - atmosphere) / transmission + atmosphere
    return _restore(result * 255, image)


def colour_transfer(image: Image.Image, *, reference: str) -> Image.Image:
    """Reinhard mean/std transfer in LAB from a reference image file."""
    with Image.open(reference) as opened:
        source = np.asarray(opened.convert("LAB"), dtype=np.float64)
    target = np.asarray(image.convert("LAB"), dtype=np.float64)
    result = np.empty_like(target)
    for channel in range(3):
        mean_source = source[..., channel].mean()
        std_source = source[..., channel].std() + 1e-6
        mean_target = target[..., channel].mean()
        std_target = target[..., channel].std() + 1e-6
        result[..., channel] = (target[..., channel] - mean_target) / std_target * std_source
        result[..., channel] += mean_source
    converted = Image.fromarray(np.clip(result, 0, 255).astype(np.uint8), "LAB").convert("RGB")
    if "A" in image.getbands():
        converted = converted.convert("RGBA")
        converted.putalpha(image.getchannel("A"))
    return converted


def histogram_match(image: Image.Image, *, reference: str) -> Image.Image:
    with Image.open(reference) as opened:
        source = np.asarray(opened.convert("RGB"), dtype=np.uint8)
    target = np.asarray(image.convert("RGB"), dtype=np.uint8)
    result = np.empty_like(target)
    for channel in range(3):
        source_values, source_counts = np.unique(source[..., channel], return_counts=True)
        target_values, target_counts = np.unique(target[..., channel], return_counts=True)
        source_cdf = np.cumsum(source_counts) / source.size
        target_cdf = np.cumsum(target_counts) / target.size
        lookup = np.zeros(256)
        lookup[target_values] = np.interp(target_cdf, source_cdf, source_values)
        result[..., channel] = np.clip(
            np.round(lookup[target[..., channel]]), 0, 255
        ).astype(np.uint8)
    converted = Image.fromarray(result, "RGB")
    if "A" in image.getbands():
        converted = converted.convert("RGBA")
        converted.putalpha(image.getchannel("A"))
    return converted


def deskew(image: Image.Image, *, limit: float = 10.0, fill: str = "0,0,0") -> Image.Image:
    """Estimate the dominant edge angle and rotate it back to level."""
    if not 0.5 <= limit <= 45:
        raise ValueError("deskew limit must be between 0.5 and 45")
    best_angle, best_score = 0.0, 0.0
    for step in range(-int(limit * 2), int(limit * 2) + 1):
        angle = step / 2
        rotated = np.asarray(
            image.convert("L").rotate(angle, resample=Image.Resampling.BILINEAR),
            dtype=np.float64,
        )
        score = float(np.var(np.abs(np.diff(rotated, axis=1)).sum(axis=0)))
        if score > best_score:
            best_angle, best_score = angle, score
    from imagen_cli.application.media_ops import _colour

    background = _colour(fill) + ((0,) if image.mode == "RGBA" else ())
    return image.rotate(
        best_angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=background
    )


OPS: dict[str, Callable[..., Image.Image]] = {
    "kuwahara": kuwahara,
    "bilateral": bilateral,
    "guided": guided,
    "nl_means": nl_means,
    "total_variation": total_variation,
    "clahe": clahe,
    "curves": curves,
    "cube_lut": cube_lut,
    "dehaze": dehaze,
    "colour_transfer": colour_transfer,
    "histogram_match": histogram_match,
    "deskew": deskew,
}


def psnr(first: Image.Image, second: Image.Image) -> float:
    left = _rgb(first)
    right = _rgb(second)
    if left.shape != right.shape:
        raise ValueError("comparison requires identical dimensions")
    error = ((left - right) ** 2).mean()
    if error == 0:
        return float("inf")
    return float(10 * np.log10(255 ** 2 / error))


def ssim(first: Image.Image, second: Image.Image) -> float:
    left = np.asarray(first.convert("L"), dtype=np.float64)
    right = np.asarray(second.convert("L"), dtype=np.float64)
    if left.shape != right.shape:
        raise ValueError("comparison requires identical dimensions")
    mu_left = left.mean()
    mu_right = right.mean()
    var_left = left.var()
    var_right = right.var()
    covariance = ((left - mu_left) * (right - mu_right)).mean()
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    numerator = (2 * mu_left * mu_right + c1) * (2 * covariance + c2)
    denominator = (mu_left ** 2 + mu_right ** 2 + c1) * (var_left + var_right + c2)
    return float(numerator / denominator)


def version() -> str:
    return str(np.__version__)
