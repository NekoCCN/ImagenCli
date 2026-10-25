"""Fast Guided Filter coefficient subsampling, He and Sun (2015), algorithm 2."""

from collections.abc import Callable

import numpy as np
from PIL import Image

from imagen_cli.application.filters_advanced import (
    _average,
    _input,
    _luma,
    _number,
    _output,
    guided_smooth,
)


def _resize(array: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    if array.ndim == 2:
        return np.asarray(Image.fromarray(array.astype(np.float32)).resize(
            size, Image.Resampling.BILINEAR), dtype=np.float64)
    return np.stack([_resize(array[..., c], size) for c in range(array.shape[2])], axis=2)


def fast_guided_smooth(image: Image.Image, *, radius: int = 4, epsilon: float = 0.001,
                       strength: float = 0.5, subsample: int = 4) -> Image.Image:
    """Luma-guided coefficient subsampling with coverage-weighted bilinear resize.

    Radius is in input pixels; low-resolution radius rounds to nearest integer,
    at least one. Coefficients, not the final image, are bilinearly upsampled.
    This Alpha-aware engineering variant does not promise full-filter equivalence.
    """
    _number("radius", radius, 1, 128, integer=True)
    _number("epsilon", epsilon, 1e-6, 1)
    _number("strength", strength, 0, 1)
    _number("subsample", subsample, 1, 8, integer=True)
    source, alpha = _input(image)
    if strength == 0:
        return image.copy()
    if subsample == 1:
        return guided_smooth(image, radius=radius, epsilon=epsilon, strength=strength)
    size = (max(1, round(image.width / subsample)), max(1, round(image.height / subsample)))
    coverage = _resize(alpha, size)
    low = np.divide(_resize(source * alpha[..., None], size), coverage[..., None],
                    out=np.zeros((*coverage.shape, 3)), where=coverage[..., None] > 1e-12)
    guide = _luma(low)[..., None]
    low_radius = max(1, round(radius / subsample))
    mean_g = _average(guide, coverage, low_radius)
    mean_p = _average(low, coverage, low_radius)
    variance = _average(guide * guide, coverage, low_radius) - mean_g * mean_g
    covariance = _average(guide * low, coverage, low_radius) - mean_g * mean_p
    a = covariance / (np.maximum(variance, 0) + epsilon)
    b = mean_p - a * mean_g
    a = _average(a, coverage, low_radius)
    b = _average(b, coverage, low_radius)
    # Interpolate supported coefficients rather than spreading zero-coverage estimates.
    support = _resize((coverage > 1e-12).astype(float), image.size)[..., None]
    a = np.divide(_resize(a * (coverage > 1e-12)[..., None], image.size), support,
                  out=np.zeros_like(source), where=support > 1e-12)
    b = np.divide(_resize(b * (coverage > 1e-12)[..., None], image.size), support,
                  out=source.copy(), where=support > 1e-12)
    filtered = a * _luma(source)[..., None] + b
    return _output(source + strength * (filtered - source), image, alpha)


OPS: dict[str, Callable[..., Image.Image]] = {"fast_guided_smooth": fast_guided_smooth}
