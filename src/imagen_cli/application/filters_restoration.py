"""Experimental restoration variants; callers supply 8-bit sRGB RGB/RGBA.

Spatial operations use alpha-weighted reflected neighborhoods; FFT uses periodic
boundaries. No ICC conversion is performed here. These engineering variants do
not claim paper-equivalent Local Laplacian, Reinhard, Retinex or LIME methods.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

import numpy as np
from PIL import Image

_LUMA = np.array([0.2126, 0.7152, 0.0722])


def _number(name: str, value: float, low: float, high: float) -> None:
    if isinstance(value, bool) or not np.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be finite and in [{low}, {high}]")


def _input(image: Image.Image) -> tuple[np.ndarray, np.ndarray]:
    if image.mode not in {"RGB", "RGBA"}:
        raise ValueError("restoration requires 8-bit sRGB RGB or RGBA input")
    pixels = np.asarray(image, dtype=np.float64) / 255
    alpha = pixels[..., 3] if image.mode == "RGBA" else np.ones(pixels.shape[:2])
    return pixels[..., :3], alpha


def _output(rgb: np.ndarray, original: Image.Image) -> Image.Image:
    output = np.rint(np.clip(rgb, 0, 1) * 255).astype(np.uint8)
    if original.mode == "RGBA":
        output = np.dstack((output, np.asarray(original)[..., 3]))
    return Image.fromarray(output)


def _gaussian(array: np.ndarray, radius: float) -> np.ndarray:
    half = int(np.ceil(3 * radius))
    x = np.arange(-half, half + 1)
    kernel = np.exp(-0.5 * (x / radius) ** 2)
    kernel /= kernel.sum()
    result = array
    for axis in (0, 1):
        padding = [(0, 0)] * array.ndim
        padding[axis] = (half, half)
        padded = np.pad(result, padding, mode="reflect")
        result = np.zeros_like(array)
        for offset, weight in enumerate(kernel):
            slices = [slice(None)] * array.ndim
            slices[axis] = slice(offset, offset + array.shape[axis])
            result += weight * padded[tuple(slices)]
    return result


def _smooth(array: np.ndarray, alpha: np.ndarray, radius: float) -> np.ndarray:
    weight = alpha if array.ndim == 2 else alpha[..., None]
    denominator = _gaussian(weight, radius)
    return cast(np.ndarray, np.divide(_gaussian(array * weight, radius), denominator,
                                     out=np.zeros_like(array), where=denominator > 1e-12))


def _decode(rgb: np.ndarray) -> np.ndarray:
    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)


def _encode(rgb: np.ndarray) -> np.ndarray:
    rgb = np.clip(rgb, 0, 1)
    return np.where(rgb <= 0.0031308, 12.92 * rgb, 1.055 * rgb ** (1 / 2.4) - 0.055)


def _gamut_luma(rgb: np.ndarray, target: np.ndarray) -> np.ndarray:
    # Compress toward neutral instead of clipping channels and shifting luminance.
    delta = rgb - (rgb @ _LUMA)[..., None]
    target = np.clip(target, 0, 1)
    limits = np.ones_like(delta)
    np.divide(1 - target[..., None], delta, out=limits, where=delta > 1e-12)
    np.divide(-target[..., None], delta, out=limits, where=delta < -1e-12)
    return cast(np.ndarray,
                target[..., None] + np.minimum(1, limits.min(axis=2))[..., None] * delta)


def bounded_sharpen(image: Image.Image, *, amount: float = 1.0, radius: float = 1.0,
                    noise_gate: float = 0.01, overshoot: float = 0.0) -> Image.Image:
    """Soft-threshold encoded-sRGB luma residual; bound to local luma extrema.

    Gate and overshoot are normalized [0,1] luma-proxy units, not sensor noise
    estimates. Final gamut compression preserves the requested luma proxy.
    """
    _number("amount", amount, 0, 4)
    _number("radius", radius, 0.3, 32)
    _number("noise_gate", noise_gate, 0, 1)
    _number("overshoot", overshoot, 0, 1)
    rgb, alpha = _input(image)
    if amount == 0:
        return image.copy()
    luma = rgb @ _LUMA
    residual = luma - _smooth(luma, alpha, radius)
    residual = np.sign(residual) * np.maximum(np.abs(residual) - noise_gate, 0)
    lower = np.full_like(luma, np.inf)
    upper = np.full_like(luma, -np.inf)
    half = int(np.ceil(radius))
    # Separable min/max avoid a quadratic radius-sized neighborhood loop.
    for axis in (0, 1):
        low_values = np.where(alpha > 0, luma, np.inf) if axis == 0 else lower
        high_values = np.where(alpha > 0, luma, -np.inf) if axis == 0 else upper
        padding = [(0, 0), (0, 0)]
        padding[axis] = (half, half)
        low_pad = np.pad(low_values, padding, mode="reflect")
        high_pad = np.pad(high_values, padding, mode="reflect")
        lower = np.full_like(luma, np.inf)
        upper = np.full_like(luma, -np.inf)
        for offset in range(2 * half + 1):
            slices = [slice(None), slice(None)]
            slices[axis] = slice(offset, offset + luma.shape[axis])
            lower = np.minimum(lower, low_pad[tuple(slices)])
            upper = np.maximum(upper, high_pad[tuple(slices)])
    lower = np.where(np.isfinite(lower), lower, luma)
    upper = np.where(np.isfinite(upper), upper, luma)
    target = np.clip(luma + amount * residual, lower - overshoot, upper + overshoot)
    return _output(_gamut_luma(rgb, target), image)


def _detail_bands(rgb: np.ndarray, alpha: np.ndarray, radius: float
                  ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Telescoping Gaussian bands; reconstruction is exact before quantization."""
    first = _smooth(rgb, alpha, radius)
    second = _smooth(rgb, alpha, radius * 2)
    base = _smooth(rgb, alpha, radius * 4)
    return rgb - first, first - second, second - base, base


def multiscale_detail(image: Image.Image, *, fine: float = 1.0, medium: float = 1.0,
                      coarse: float = 1.0, radius: float = 1.0) -> Image.Image:
    """Three reconstructable Gaussian detail bands, not edge-preserving WLS.

    Gain edits use an encoded-sRGB luma proxy; base brightness is unchanged.
    """
    for name, value in (("fine", fine), ("medium", medium), ("coarse", coarse)):
        _number(name, value, 0, 4)
    _number("radius", radius, 0.3, 32)
    rgb, alpha = _input(image)
    if fine == medium == coarse == 1:
        return image.copy()
    bands = _detail_bands((rgb @ _LUMA)[..., None], alpha, radius)
    target = (fine * bands[0] + medium * bands[1] + coarse * bands[2] + bands[3])[..., 0]
    return _output(_gamut_luma(rgb, target), image)


def periodic_notch(image: Image.Image, *, frequencies: str = "", width: float = 0.01,
                   strength: float = 1.0) -> Image.Image:
    """Manual Gaussian notch pairs in cycles/pixel: 'fx,fy;fx,fy'.

    Frequencies are periodic coordinates in [-0.5,0.5]; DC cannot be selected.
    Conjugate pairs and DC preservation are enforced. Intentional patterns at a
    selected frequency are removed too; this is not universal automatic repair.
    """
    _number("width", width, 0.0001, 0.25)
    _number("strength", strength, 0, 1)
    try:
        centers = [tuple(float(v) for v in pair.split(","))
                   for pair in frequencies.split(";") if pair.strip()]
    except (AttributeError, ValueError) as error:
        raise ValueError("frequencies must be fx,fy;fx,fy in cycles/pixel") from error
    for center in centers:
        if len(center) != 2 or not np.isfinite(center).all():
            raise ValueError("each notch needs two finite frequencies")
        if max(abs(v) for v in center) > 0.5 or center == (0, 0):
            raise ValueError("frequency must be in [-0.5,0.5], excluding DC")
    rgb, alpha = _input(image)
    if not centers or strength == 0:
        return image.copy()
    fy = np.fft.fftfreq(image.height)[:, None]
    fx = np.fft.fftfreq(image.width)[None, :]
    mask = np.ones(alpha.shape)
    for cx, cy in centers:
        pair = np.ones_like(mask)
        for sign in (-1, 1):
            dx = (fx - sign * cx + 0.5) % 1 - 0.5
            dy = (fy - sign * cy + 0.5) % 1 - 0.5
            pair *= 1 - np.exp(-(dx * dx + dy * dy) / (2 * width * width))
        mask *= 1 - strength * (1 - pair)
    mask[0, 0] = 1
    # Hidden RGB must never inject global spectral energy into visible pixels.
    visible = rgb * alpha[..., None]
    filtered = np.fft.ifft2(np.fft.fft2(visible, axes=(0, 1)) * mask[..., None],
                            axes=(0, 1)).real
    correction = filtered - visible
    return _output(rgb + correction, image)


def _statistics(values: np.ndarray, alpha: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    total: float = float(alpha.sum())
    if total <= 1e-12:
        raise ValueError("chroma statistics require visible pixels")
    mean = np.sum(values * alpha[..., None], axis=(0, 1)) / total
    variance = np.sum((values - mean) ** 2 * alpha[..., None], axis=(0, 1)) / total
    return mean, np.sqrt(variance)


def chroma_transfer(image: Image.Image, *, reference: str | Path | Image.Image,
                    strength: float = 1.0, luminance: str = "preserve") -> Image.Image:
    """Alpha-weighted linear-RGB opponent chroma mean/std transfer.

    'preserve' keeps linear relative luminance Y, not Lab perceptual lightness;
    'match' also matches reference Y statistics. Zero source variance maps to the
    reference mean. Neutral-axis gamut compression preserves Y before encoding.
    """
    _number("strength", strength, 0, 1)
    if luminance not in {"preserve", "match"}:
        raise ValueError("luminance must be preserve or match")
    from imagen_cli.application.postprocess_service import normalize_color

    rgb, alpha = _input(image)
    if isinstance(reference, Image.Image):
        normalized, _ = normalize_color(reference)
        ref, ref_alpha = _input(normalized)
    else:
        with Image.open(reference) as opened:
            normalized, _ = normalize_color(opened)
            ref, ref_alpha = _input(normalized)
    linear = _decode(rgb)
    reference_linear = _decode(ref)

    def opponent(array: np.ndarray) -> np.ndarray:
        return np.stack((array @ _LUMA, array[..., 0] - array[..., 1],
                         array[..., 2] - array[..., 1]), axis=2)

    source = opponent(linear)
    target = opponent(reference_linear)
    mean, std = _statistics(source, alpha)
    ref_mean, ref_std = _statistics(target, ref_alpha)
    if strength == 0:
        return image.copy()
    transferred = (source - mean) * np.divide(ref_std, std, out=np.zeros(3),
                                             where=std > 1e-8) + ref_mean
    if luminance == "preserve":
        transferred[..., 0] = source[..., 0]
    mixed = source + strength * (transferred - source)
    y, red_green, blue_green = np.moveaxis(mixed, -1, 0)
    green = y - _LUMA[0] * red_green - _LUMA[2] * blue_green
    result = np.stack((green + red_green, green, green + blue_green), axis=2)
    return _output(_encode(_gamut_luma(result, y)), image)


def linear_resize(image: Image.Image, *, width: int | None = None,
                  height: int | None = None, scale: float | None = None,
                  filter: str = "lanczos") -> Image.Image:
    """Resize float linear sRGB premultiplied by linear coverage, then encode.

    Negative-lobed kernels are bounded to valid premultiplied color/coverage.
    Exact same-size requests bypass conversion, preserving hidden RGB as well.
    """
    filters = {"nearest": Image.Resampling.NEAREST, "box": Image.Resampling.BOX,
               "bilinear": Image.Resampling.BILINEAR, "bicubic": Image.Resampling.BICUBIC,
               "lanczos": Image.Resampling.LANCZOS}
    if filter not in filters:
        raise ValueError("unknown resize filter")
    for name, value in (("width", width), ("height", height)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)
                                  or value <= 0):
            raise ValueError(f"{name} must be a positive integer")
    if scale is not None:
        _number("scale", scale, 1e-6, 1e6)
        if width is not None or height is not None:
            raise ValueError("scale and explicit dimensions are mutually exclusive")
        width, height = round(image.width * scale), round(image.height * scale)
    elif width is None and height is None:
        raise ValueError("resize needs width, height or scale")
    elif width is None:
        assert height is not None
        width = round(image.width * height / image.height)
    elif height is None:
        height = round(image.height * width / image.width)
    assert width is not None and height is not None
    if width <= 0 or height <= 0:
        raise ValueError("target dimensions must be positive")
    rgb, alpha = _input(image)
    if (width, height) == image.size:
        return image.copy()

    def resize_plane(plane: np.ndarray) -> np.ndarray:
        return np.asarray(Image.fromarray(plane.astype(np.float32)).resize(
            (width, height), filters[filter]), dtype=np.float64)

    coverage = np.clip(resize_plane(alpha), 0, 1)
    premultiplied = _decode(rgb) * alpha[..., None]
    resized = np.stack([resize_plane(premultiplied[..., c]) for c in range(3)], axis=2)
    resized = np.clip(resized, 0, coverage[..., None])
    straight = np.divide(resized, coverage[..., None], out=np.zeros_like(resized),
                         where=coverage[..., None] >= 1e-6)
    output = np.rint(_encode(straight) * 255).astype(np.uint8)
    if image.mode == "RGBA":
        output = np.dstack((output, np.rint(coverage * 255).astype(np.uint8)))
    return Image.fromarray(output)


def illumination_correct(image: Image.Image, *, radius: float = 16.0,
                         strength: float = 0.5, floor: float = 0.05) -> Image.Image:
    """Approximate smooth-illumination division in linear sRGB, not LIME.

    Gaussian max-RGB illumination is normalized to its coverage-weighted mean.
    This can flatten intended shading/material differences and amplify noise.
    """
    _number("radius", radius, 0.3, 128)
    _number("strength", strength, 0, 1)
    _number("floor", floor, 0.001, 1)
    rgb, alpha = _input(image)
    if strength == 0 or alpha.sum() == 0:
        return image.copy()
    linear = _decode(rgb)
    illumination = np.maximum(_smooth(linear.max(axis=2), alpha, radius), floor)
    target = np.sum(illumination * alpha) / alpha.sum()
    corrected = linear * (target / illumination)[..., None]
    return _output(_encode(linear + strength * (corrected - linear)), image)


OPS: dict[str, Callable[..., Image.Image]] = {
    "bounded_sharpen": bounded_sharpen,
    "multiscale_detail": multiscale_detail,
    "periodic_notch": periodic_notch,
    "chroma_transfer": chroma_transfer,
    "linear_resize": linear_resize,
    "illumination_correct": illumination_correct,
}
