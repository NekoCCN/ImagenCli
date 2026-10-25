"""Weighted median reference variant, not the CVPR 2014 accelerated algorithm."""

from collections.abc import Callable

import numpy as np
from PIL import Image

from imagen_cli.application.filters_advanced import _input, _number, _output


def weighted_median(image: Image.Image, *, radius: int = 2,
                    sigma_range: float = 0.15, strength: float = 1.0) -> Image.Image:
    """Per-channel weighted rank with RGB Gaussian affinity and Alpha coverage.

    Uses the first sorted value reaching half total weight (Zhang et al. 2014,
    eq. 2). No feature quantization, joint histogram or median tracking. Spatial
    support is a reflected square, not a separable or downsampled approximation.
    """
    _number("radius", radius, 1, 8, integer=True)
    _number("sigma_range", sigma_range, 0.001, 1)
    _number("strength", strength, 0, 1)
    source, alpha = _input(image)
    if strength == 0:
        return image.copy()
    padded = np.pad(source, ((radius, radius), (radius, radius), (0, 0)), mode="reflect")
    coverage = np.pad(alpha, ((radius, radius), (radius, radius)), mode="reflect")
    result = source.copy()
    size = radius * 2 + 1
    # Row-local sorting bounds temporary storage; full-image window tensors do not scale.
    for y in range(image.height):
        windows = np.lib.stride_tricks.sliding_window_view(
            padded[y:y + size], (size, size), axis=(0, 1)
        )[0].transpose(0, 2, 3, 1).reshape(image.width, size * size, 3)
        weights = np.lib.stride_tricks.sliding_window_view(
            coverage[y:y + size], (size, size)
        )[0].reshape(image.width, size * size)
        distance = np.sum((windows - source[y, :, None]) ** 2, axis=2)
        weights = weights * np.exp(-distance / (2 * sigma_range**2))
        half = weights.sum(axis=1) / 2
        for channel in range(3):
            values = windows[..., channel]
            order = np.argsort(values, axis=1, kind="stable")
            sorted_values = np.take_along_axis(values, order, axis=1)
            cumulative = np.take_along_axis(weights, order, axis=1).cumsum(axis=1)
            selected = np.argmax(cumulative >= half[:, None], axis=1)
            result[y, :, channel] = sorted_values[np.arange(image.width), selected]
    return _output(source + strength * (result - source), image, alpha)


OPS: dict[str, Callable[..., Image.Image]] = {"weighted_median": weighted_median}
