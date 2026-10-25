"""Experimental narrow spectral peak detector, not semantic artifact recognition."""

from collections.abc import Callable

import numpy as np
from PIL import Image

from imagen_cli.application.filters_advanced import _input, _luma, _number
from imagen_cli.application.filters_restoration import periodic_notch


def detect_periodic_peaks(image: Image.Image, *, threshold: float = 8,
                          min_frequency: float = 0.06,
                          max_pairs: int = 3) -> list[tuple[float, float]]:
    """Hann-windowed plane-detrended spectrum, local narrow-peak contrast.

    A detection is evidence of periodic structure, never evidence it is unwanted.
    Frequencies are cycles per input pixel; conjugate pairs are returned once.
    """
    _number("threshold", threshold, 2, 100)
    _number("min_frequency", min_frequency, 0.01, 0.45)
    _number("max_pairs", max_pairs, 1, 16, integer=True)
    rgb, alpha = _input(image)
    height, width = alpha.shape
    if min(height, width) < 8 or alpha.sum() < 1e-12:
        return []
    yy, xx = np.mgrid[:height, :width]
    design = np.stack((np.ones_like(xx), xx / max(width - 1, 1),
                       yy / max(height - 1, 1)), axis=2).astype(float)
    luma = _luma(rgb)
    weight = np.sqrt(alpha.ravel())
    coefficients = np.linalg.lstsq(design.reshape(-1, 3) * weight[:, None],
                                    luma.ravel() * weight, rcond=None)[0]
    residual = (luma - design @ coefficients) * alpha
    if np.max(np.abs(residual)) < 0.5 / 255:
        return []
    window = np.hanning(height)[:, None] * np.hanning(width)[None, :]
    amplitude = np.abs(np.fft.fft2(residual * window))
    fx, fy = np.fft.fftfreq(width)[None, :], np.fft.fftfreq(height)[:, None]
    eligible = np.hypot(fx, fy) >= min_frequency
    # Suppress leakage shoulders; only isolated maxima relative to an annulus qualify.
    background = np.zeros_like(amplitude)
    count = 0
    maximum = np.ones_like(eligible)
    for dy in range(-3, 4):
        for dx in range(-3, 4):
            shifted = np.roll(amplitude, (dy, dx), axis=(0, 1))
            if max(abs(dx), abs(dy)) == 3:
                background += shifted
                count += 1
            if (dx or dy) and max(abs(dx), abs(dy)) <= 1:
                maximum &= amplitude >= shifted
    background /= count
    floor = max(float(amplitude.max()) * 1e-6, 1e-12)
    eligible &= maximum & (amplitude > threshold * np.maximum(background, floor))
    eligible &= amplitude > amplitude.max() * 0.15
    order = np.argsort(np.where(eligible, amplitude, 0).ravel())[::-1]
    peaks: list[tuple[float, float]] = []
    for index in order:
        y, x = np.unravel_index(index, amplitude.shape)
        if not eligible[y, x]:
            break
        cx, cy = float(fx[0, x]), float(fy[y, 0])
        if cy < 0 or (cy == 0 and cx < 0):
            cx, cy = -cx, -cy
        if any(np.hypot(cx - px, cy - py) < 3 / min(width, height) for px, py in peaks):
            continue
        peaks.append((cx, cy))
        if len(peaks) == max_pairs:
            break
    return peaks


def periodic_notch_auto(image: Image.Image, *, threshold: float = 8,
                        min_frequency: float = 0.06, max_pairs: int = 3,
                        width: float = 0.01, strength: float = 0.5) -> Image.Image:
    _number("width", width, 0.0001, 0.25)
    _number("strength", strength, 0, 1)
    peaks = detect_periodic_peaks(image, threshold=threshold,
                                   min_frequency=min_frequency, max_pairs=max_pairs)
    if not peaks or strength == 0:
        result = image.copy()
    else:
        frequencies = ";".join(f"{fx:.17g},{fy:.17g}" for fx, fy in peaks)
        result = periodic_notch(image, frequencies=frequencies, width=width, strength=strength)
    result.info["postprocess_detection"] = {"frequencies": [list(pair) for pair in peaks],
                                           "units": "cycles_per_pixel",
                                           "semantic_confidence": "unknown"}
    return result


OPS: dict[str, Callable[..., Image.Image]] = {"periodic_notch_auto": periodic_notch_auto}
