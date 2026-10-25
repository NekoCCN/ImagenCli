"""Global structure separation: periodic L0, recursive domain transform and WLS/RTV."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import numpy as np
from PIL import Image


def _input(image: Image.Image, strength: float) -> np.ndarray[Any, Any]:
    if not math.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError("strength must be finite and between 0 and 1")
    rgb = np.asarray(image.convert("RGB"), dtype=np.float64) / 255
    if "A" in image.getbands():
        coverage = np.asarray(image.getchannel("A"), dtype=np.float64) / 255
        # Global solvers fill zero-coverage pixels with the visible weighted mean,
        # not hidden RGB; this is an extension policy, not a masked-domain solve.
        mean = np.sum(rgb * coverage[..., None], axis=(0, 1)) / max(coverage.sum(), 1e-12)
        rgb[coverage == 0] = mean
    return rgb


def _output(array: Any, original: Any, image: Image.Image, strength: float) -> Image.Image:
    pixels = np.rint(np.clip(original + strength * (array - original), 0, 1) * 255).astype(
        np.uint8
    )
    if "A" in image.getbands():
        hidden = np.asarray(image.getchannel("A")) == 0
        pixels[hidden] = np.asarray(image.convert("RGB"))[hidden]
    result = Image.fromarray(pixels)
    if "A" in image.getbands():
        result = result.convert("RGBA")
        result.putalpha(image.getchannel("A"))
    return result


def l0_smooth(
    image: Image.Image, *, smoothing: float = 0.02, beta_growth: float = 2,
    strength: float = 1,
) -> Image.Image:
    """Half-quadratic L0 gradient minimization with explicitly periodic boundaries."""
    original = _input(image, strength)
    if not math.isfinite(smoothing) or not 0 < smoothing <= 1:
        raise ValueError("smoothing must be between 0 and 1")
    if not math.isfinite(beta_growth) or not 1.1 <= beta_growth <= 4:
        raise ValueError("beta_growth must be between 1.1 and 4")
    if strength == 0:
        return image.copy()
    height, width = original.shape[:2]
    frequencies = (4 * np.sin(np.pi * np.fft.fftfreq(height))[:, None] ** 2
                   + 4 * np.sin(np.pi * np.fft.fftfreq(width))[None, :] ** 2)
    numerator = np.fft.fft2(original, axes=(0, 1))
    result = original.copy()
    beta = 2 * smoothing
    while beta < 1e5:
        dx = np.roll(result, -1, axis=1) - result
        dy = np.roll(result, -1, axis=0) - result
        small = np.sum(dx * dx + dy * dy, axis=2) < smoothing / beta
        dx[small] = 0
        dy[small] = 0
        divergence = np.roll(dx, 1, axis=1) - dx + np.roll(dy, 1, axis=0) - dy
        result = np.fft.ifft2(
            (numerator + beta * np.fft.fft2(divergence, axes=(0, 1)))
            / (1 + beta * frequencies[..., None]), axes=(0, 1)
        ).real
        beta *= beta_growth
    return _output(result, original, image, strength)


def domain_transform(
    image: Image.Image, *, sigma_space: float = 8, sigma_range: float = 0.15,
    iterations: int = 3, strength: float = 1,
) -> Image.Image:
    """Recursive transformed-domain filtering; full-image fixed-guide scan schedule."""
    original = _input(image, strength)
    if not math.isfinite(sigma_space) or not 0 < sigma_space <= 256:
        raise ValueError("invalid sigma_space")
    if not math.isfinite(sigma_range) or not 0 < sigma_range <= 1:
        raise ValueError("invalid sigma_range")
    if type(iterations) is not int or not 1 <= iterations <= 8:
        raise ValueError("invalid iterations")
    if strength == 0:
        return image.copy()
    horizontal = 1 + sigma_space / sigma_range * np.abs(np.diff(original, axis=1)).sum(2)
    vertical = 1 + sigma_space / sigma_range * np.abs(np.diff(original, axis=0)).sum(2)
    result = original.copy()
    for iteration in range(iterations):
        sigma = sigma_space * math.sqrt(3) * 2 ** (iterations - iteration - 1)
        sigma /= math.sqrt(4 ** iterations - 1)
        ax = np.exp(-math.sqrt(2) / sigma * horizontal)[..., None]
        ay = np.exp(-math.sqrt(2) / sigma * vertical)[..., None]
        for x in range(1, result.shape[1]):
            result[:, x] += ax[:, x - 1] * (result[:, x - 1] - result[:, x])
        for x in range(result.shape[1] - 2, -1, -1):
            result[:, x] += ax[:, x] * (result[:, x + 1] - result[:, x])
        for y in range(1, result.shape[0]):
            result[y] += ay[y - 1] * (result[y - 1] - result[y])
        for y in range(result.shape[0] - 2, -1, -1):
            result[y] += ay[y] * (result[y + 1] - result[y])
    return _output(result, original, image, strength)


def _solve(original: Any, wx: Any, wy: Any, tolerance: float = 1e-6) -> Any:
    """Matrix-free conjugate gradient for symmetric positive definite I + D'WD."""
    def multiply(value: Any) -> Any:
        out = value.copy()
        dx = wx[..., None] * (value[:, :-1] - value[:, 1:])
        dy = wy[..., None] * (value[:-1] - value[1:])
        out[:, :-1] += dx
        out[:, 1:] -= dx
        out[:-1] += dy
        out[1:] -= dy
        return out

    result = original.copy()
    residual = original - multiply(result)
    direction = residual.copy()
    squared = float(np.sum(residual * residual))
    threshold = tolerance ** 2 * max(float(np.sum(original * original)), 1e-12)
    for _ in range(500):
        if squared <= threshold:
            return result
        product = multiply(direction)
        step = squared / max(float(np.sum(direction * product)), 1e-30)
        result += step * direction
        residual -= step * product
        updated = float(np.sum(residual * residual))
        direction = residual + (updated / max(squared, 1e-30)) * direction
        squared = updated
    if squared > threshold:
        raise ValueError("structure solver did not converge in 500 iterations")
    return result


def wls_smooth(
    image: Image.Image, *, smoothing: float = 0.1, alpha: float = 1.2,
    strength: float = 1,
) -> Image.Image:
    """Log-luminance weighted least-squares smoothing, natural edge boundaries."""
    original = _input(image, strength)
    if not math.isfinite(smoothing) or not 0 < smoothing <= 10:
        raise ValueError("invalid smoothing")
    if not math.isfinite(alpha) or not 0.1 <= alpha <= 2:
        raise ValueError("invalid alpha")
    if strength == 0:
        return image.copy()
    guide = np.log(np.maximum(original @ np.array([0.2126, 0.7152, 0.0722]), 1e-4))
    wx = smoothing / (np.abs(np.diff(guide, axis=1)) ** alpha + 1e-4)
    wy = smoothing / (np.abs(np.diff(guide, axis=0)) ** alpha + 1e-4)
    return _output(_solve(original, wx, wy), original, image, strength)


def _box(array: Any, radius: int = 2) -> Any:
    padded = np.pad(array, ((radius, radius), (radius, radius)), mode="edge")
    integral = np.pad(padded, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    size = 2 * radius + 1
    return (integral[size:, size:] - integral[:-size, size:]
            - integral[size:, :-size] + integral[:-size, :-size]) / size ** 2


def rtv_smooth(
    image: Image.Image, *, smoothing: float = 0.01, iterations: int = 4,
    strength: float = 1,
) -> Image.Image:
    """RTV-inspired IRLS with box-window variation weights (explicit approximation)."""
    original = _input(image, strength)
    if not math.isfinite(smoothing) or not 0 < smoothing <= 1:
        raise ValueError("invalid smoothing")
    if type(iterations) is not int or not 1 <= iterations <= 10:
        raise ValueError("invalid iterations")
    if strength == 0:
        return image.copy()
    result = original.copy()
    for _ in range(iterations):
        guide = result @ np.array([0.2126, 0.7152, 0.0722])
        dx = np.pad(np.diff(guide, axis=1), ((0, 0), (0, 1)))
        dy = np.pad(np.diff(guide, axis=0), ((0, 1), (0, 0)))
        wx = smoothing / (np.abs(_box(dx)) + 0.01) / (np.abs(dx) + 0.01)
        wy = smoothing / (np.abs(_box(dy)) + 0.01) / (np.abs(dy) + 0.01)
        result = _solve(original, wx[:, :-1], wy[:-1])
    return _output(result, original, image, strength)


def wls_detail(image: Image.Image, *, fine: float = 1, medium: float = 1,
               coarse: float = 1, smoothing: float = 0.05,
               scale_ratio: float = 4, alpha: float = 1.2) -> Image.Image:
    """Fixed-guide WLS scale bands in encoded-sRGB luma, not Local Laplacian.

    Three natural-boundary CG solves share the original log-luma edge weights.
    Gamut compression follows luma editing; unity bypasses quantization exactly.
    """
    from imagen_cli.application.filters_advanced import _number
    from imagen_cli.application.filters_restoration import _gamut_luma

    for name, value in (("fine", fine), ("medium", medium), ("coarse", coarse)):
        _number(name, value, 0, 4)
    _number("smoothing", smoothing, 0.0001, 1)
    _number("scale_ratio", scale_ratio, 1.1, 8)
    _number("alpha", alpha, 0.1, 2)
    original = _input(image, 1)
    if fine == medium == coarse == 1:
        return image.copy()
    luma = original @ np.array([0.2126, 0.7152, 0.0722])
    guide = np.log(np.maximum(luma, 1e-4))
    wx = smoothing / (np.abs(np.diff(guide, axis=1)) ** alpha + 1e-4)
    wy = smoothing / (np.abs(np.diff(guide, axis=0)) ** alpha + 1e-4)
    layers = [_solve(luma[..., None], wx * scale_ratio**i, wy * scale_ratio**i)[..., 0]
              for i in range(3)]
    target = (fine * (luma - layers[0]) + medium * (layers[0] - layers[1])
              + coarse * (layers[1] - layers[2]) + layers[2])
    return _output(_gamut_luma(original, target), original, image, 1)


OPS: dict[str, Callable[..., Image.Image]] = {
    "wls_detail": wls_detail,
    "l0_smooth": l0_smooth,
    "domain_transform": domain_transform,
    "wls_smooth": wls_smooth,
    "rtv_smooth": rtv_smooth,
}
