"""Optional NumPy CPU reference filters, operating in encoded sRGB.

Independent implementations of He et al. (2010), Zhang et al. (2014),
Kang et al. (2007), and Kyprianidis et al. (2009). FDoG uses bilinear,
one-pixel Euler flow integration; Kuwahara uses smooth angular sector weights
rather than the paper's precomputed convolved sector masks. These are reference
variants, not reproductions of the authors' accelerated implementations.
"""

from __future__ import annotations

from collections.abc import Callable
from math import ceil
from numbers import Integral, Real
from typing import cast

import numpy as np
from PIL import Image

Array = np.ndarray


def _number(name: str, value: float, low: float, high: float, *, integer: bool = False) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not np.isfinite(value)
        or not low <= value <= high
        or (integer and not isinstance(value, Integral))
    ):
        raise ValueError(f"{name} must be {'an integer ' if integer else ''}in [{low}, {high}]")


def _input(image: Image.Image) -> tuple[Array, Array]:
    if image.mode not in {"RGB", "RGBA"}:
        raise ValueError("advanced filters require RGB or RGBA input")
    rgb = np.asarray(image, dtype=np.float64)[..., :3] / 255
    alpha = (
        np.asarray(image.getchannel("A"), dtype=np.float64) / 255
        if image.mode == "RGBA"
        else np.ones(rgb.shape[:2])
    )
    return rgb, alpha


def _output(values: Array, original: Image.Image, alpha: Array) -> Image.Image:
    # Uncovered RGB is kept byte-for-byte, but never participates in statistics.
    values = np.where(alpha[..., None] > 0, values, _input(original)[0])
    result = Image.fromarray(np.clip(np.rint(values * 255), 0, 255).astype(np.uint8))
    if original.mode == "RGBA":
        result = result.convert("RGBA")
        result.putalpha(original.getchannel("A"))
    return result


def _luma(rgb: Array) -> Array:
    return cast(Array, rgb @ np.array([0.299, 0.587, 0.114]))


def _box(array: Array, radius: int) -> Array:
    """Reflect-padded box sums with float64 integral accumulation, O(N)."""
    pads = ((radius, radius), (radius, radius)) + ((0, 0),) * (array.ndim - 2)
    padded = np.pad(array, pads, mode="reflect")
    integral = np.pad(padded, ((1, 0), (1, 0)) + ((0, 0),) * (array.ndim - 2))
    integral = integral.cumsum(axis=0, dtype=np.float64).cumsum(axis=1, dtype=np.float64)
    size = radius * 2 + 1
    return cast(
        Array,
        integral[size:, size:]
        - integral[:-size, size:]
        - integral[size:, :-size]
        + integral[:-size, :-size],
    )


def _average(array: Array, alpha: Array, radius: int) -> Array:
    weight = alpha.reshape(alpha.shape + (1,) * (array.ndim - 2))
    denominator = _box(weight, radius)
    return cast(
        Array,
        np.divide(
            _box(array * weight, radius),
            denominator,
            out=np.zeros_like(array),
            where=denominator > 1e-12,
        ),
    )


def _gaussian(array: Array, sigma: float) -> Array:
    radius = ceil(3 * sigma)
    kernel = np.exp(-(np.arange(-radius, radius + 1, dtype=np.float64) ** 2) / (2 * sigma**2))
    kernel /= kernel.sum()
    result = array
    for axis in (0, 1):
        pads = [(0, 0)] * array.ndim
        pads[axis] = (radius, radius)
        padded = np.pad(result, pads, mode="reflect")
        result = np.zeros_like(array)
        for index, weight in enumerate(kernel):
            slices = [slice(None)] * array.ndim
            slices[axis] = slice(index, index + array.shape[axis])
            result += weight * padded[tuple(slices)]
    return result


def _weighted_gaussian(array: Array, alpha: Array, sigma: float) -> Array:
    weight = alpha.reshape(alpha.shape + (1,) * (array.ndim - 2))
    den = _gaussian(weight, sigma)
    return cast(
        Array,
        np.divide(
            _gaussian(array * weight, sigma), den, out=np.zeros_like(array), where=den > 1e-12
        ),
    )


def _gradient(array: Array) -> tuple[Array, Array]:
    # numpy.gradient needs two samples per axis; singleton images are legitimate.
    gy = np.gradient(array, axis=0) if array.shape[0] > 1 else np.zeros_like(array)
    gx = np.gradient(array, axis=1) if array.shape[1] > 1 else np.zeros_like(array)
    return gy, gx


def guided_smooth(
    image: Image.Image,
    *,
    radius: int = 4,
    epsilon: float = 0.001,
    strength: float = 0.5,
    guidance: str = "luma",
    border: str = "reflect",
) -> Image.Image:
    """Coverage-weighted full-resolution local linear guided smoothing.

    epsilon is in normalized guide-value squared units; RGB guidance solves a
    regularized 3x3 covariance system rather than filtering channels separately.
    """
    _number("radius", radius, 1, 128, integer=True)
    _number("epsilon", epsilon, 1e-6, 1)
    _number("strength", strength, 0, 1)
    if guidance not in {"luma", "rgb"} or border != "reflect":
        raise ValueError("guided_smooth requires luma/rgb guidance and reflect border")
    source, alpha = _input(image)
    if strength == 0:
        return image.copy()
    guide = _luma(source)[..., None] if guidance == "luma" else source
    mean_g = _average(guide, alpha, radius)
    mean_p = _average(source, alpha, radius)
    cross = _average(guide[..., :, None] * source[..., None, :], alpha, radius)
    covariance = (
        _average(guide[..., :, None] * guide[..., None, :], alpha, radius)
        - mean_g[..., :, None] * mean_g[..., None, :]
    )
    covariance += epsilon * np.eye(guide.shape[-1])
    coefficients = np.linalg.solve(covariance, cross - mean_g[..., :, None] * mean_p[..., None, :])
    intercept = mean_p - np.einsum("...ij,...i->...j", coefficients, mean_g)
    filtered = np.einsum(
        "...ij,...i->...j", _average(coefficients, alpha, radius), guide
    ) + _average(intercept, alpha, radius)
    return _output(source * (1 - strength) + filtered * strength, image, alpha)


def _joint_bilateral(
    source: Array, guide: Array, alpha: Array, sigma_space: float, sigma_range: float
) -> Array:
    radius = ceil(3 * sigma_space)
    pads = ((radius, radius), (radius, radius))
    values = np.pad(source, pads + ((0, 0),), mode="reflect")
    guidance = np.pad(guide, pads + ((0, 0),), mode="reflect")
    coverage = np.pad(alpha, pads, mode="reflect")
    height, width = source.shape[:2]
    total, weights = np.zeros_like(source), np.zeros_like(alpha)
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            ys, xs = (
                slice(radius + dy, radius + dy + height),
                slice(radius + dx, radius + dx + width),
            )
            distance = ((guidance[ys, xs] - guide) ** 2).sum(axis=2)
            weight = (
                np.exp(
                    -(dx * dx + dy * dy) / (2 * sigma_space**2) - distance / (2 * sigma_range**2)
                )
                * coverage[ys, xs]
            )
            total += values[ys, xs] * weight[..., None]
            weights += weight
    return cast(
        Array,
        np.divide(total, weights[..., None], out=source.copy(), where=weights[..., None] > 1e-12),
    )


def rolling_guidance(
    image: Image.Image,
    *,
    sigma_space: float = 3,
    sigma_range: float = 0.1,
    iterations: int = 4,
    strength: float = 0.5,
    inner: str = "joint_bilateral",
    border: str = "reflect",
) -> Image.Image:
    """Gaussian initialization followed by joint bilateral on the fixed RGB input.

    Each update uses the previous RGB output only as the range guide. The finite
    spatial kernel is truncated at ceil(3*sigma_space), not a subsampled preview.
    """
    _number("sigma_space", sigma_space, 0.5, 32)
    _number("sigma_range", sigma_range, 0.001, 1)
    _number("iterations", iterations, 1, 10, integer=True)
    _number("strength", strength, 0, 1)
    if inner != "joint_bilateral" or border != "reflect":
        raise ValueError("rolling_guidance requires joint_bilateral and reflect border")
    source, alpha = _input(image)
    if strength == 0:
        return image.copy()
    guide = _weighted_gaussian(source, alpha, sigma_space)
    for _ in range(iterations):
        guide = _joint_bilateral(source, guide, alpha, sigma_space, sigma_range)
    return _output(source * (1 - strength) + guide * strength, image, alpha)


def _reflect(coordinate: Array, length: int) -> Array:
    if length == 1:
        return np.zeros_like(coordinate)
    folded = np.mod(coordinate, 2 * (length - 1))
    return cast(Array, np.where(folded <= length - 1, folded, 2 * (length - 1) - folded))


def _sample(array: Array, y: Array, x: Array) -> Array:
    """Bilinear reflect sampling, including subpixel positions and singleton axes."""
    y, x = _reflect(y, array.shape[0]), _reflect(x, array.shape[1])
    y0, x0 = np.floor(y).astype(int), np.floor(x).astype(int)
    y1, x1 = np.minimum(y0 + 1, array.shape[0] - 1), np.minimum(x0 + 1, array.shape[1] - 1)
    fy, fx = y - y0, x - x0
    for _ in range(array.ndim - 2):
        fy, fx = fy[..., None], fx[..., None]
    return cast(
        Array,
        (array[y0, x0] * (1 - fx) + array[y0, x1] * fx) * (1 - fy)
        + (array[y1, x0] * (1 - fx) + array[y1, x1] * fx) * fy,
    )


def _etf(luma: Array, alpha: Array, radius: int, iterations: int) -> tuple[Array, Array]:
    gy, gx = _gradient(luma)
    magnitude = np.hypot(gx, gy) * alpha
    magnitude /= max(float(magnitude.max()), 1e-12)
    tangent = np.stack((-gy, gx), axis=2)  # x,y
    norm = np.linalg.norm(tangent, axis=2)
    tangent /= np.maximum(norm[..., None], 1e-12)
    yy, xx = np.mgrid[: luma.shape[0], : luma.shape[1]]
    for _ in range(iterations):
        updated = np.zeros_like(tangent)
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if dx * dx + dy * dy > radius * radius:
                    continue
                neighbor = _sample(tangent, yy + dy, xx + dx)
                dot = np.sum(tangent * neighbor, axis=2)
                # Kang et al.: sign alignment, angular similarity, gradient weight.
                weight = (
                    (1 + np.tanh(_sample(magnitude, yy + dy, xx + dx) - magnitude))
                    * np.abs(dot)
                    * np.where(dot < 0, -1, 1)
                    * _sample(alpha, yy + dy, xx + dx)
                )
                updated += neighbor * weight[..., None]
        norm = np.linalg.norm(updated, axis=2)
        tangent = np.divide(
            updated, norm[..., None], out=tangent.copy(), where=norm[..., None] > 1e-12
        )
    return tangent, magnitude


def _normal_dog(
    luma: Array, alpha: Array, tangent: Array, sigma: float, ratio: float, tau: float
) -> Array:
    yy, xx = np.mgrid[: luma.shape[0], : luma.shape[1]]
    numerator = [np.zeros_like(luma), np.zeros_like(luma)]
    denominator = [np.zeros_like(luma), np.zeros_like(luma)]
    radius = ceil(3 * sigma * ratio)
    for step in range(-radius, radius + 1):
        y, x = yy + step * tangent[..., 0], xx - step * tangent[..., 1]
        coverage = _sample(alpha, y, x)
        value = _sample(luma * alpha, y, x)
        for index, scale in enumerate((sigma, sigma * ratio)):
            weight = np.exp(-step * step / (2 * scale**2))
            numerator[index] += value * weight
            denominator[index] += coverage * weight
    means = [
        np.divide(n, d, out=np.zeros_like(n), where=d > 1e-12)
        for n, d in zip(numerator, denominator, strict=True)
    ]
    return cast(Array, means[0] - tau * means[1])


def _flow_integrate(response: Array, alpha: Array, tangent: Array, sigma: float) -> Array:
    yy, xx = np.mgrid[: response.shape[0], : response.shape[1]]
    total, weight_sum = response * alpha, alpha.copy()
    for sign in (-1, 1):
        y, x = yy.astype(float), xx.astype(float)
        direction = tangent * sign
        active = np.linalg.norm(direction, axis=2) > 1e-8
        for step in range(1, ceil(3 * sigma) + 1):
            x, y = x + direction[..., 0], y + direction[..., 1]
            sampled = _sample(tangent, y, x)
            # A tangent field is unoriented: retain the integration direction.
            dot = np.sum(sampled * direction, axis=2)
            sampled *= np.where(dot < 0, -1, 1)[..., None]
            norm = np.linalg.norm(sampled, axis=2)
            active &= norm > 1e-8
            direction = sampled / np.maximum(norm[..., None], 1e-12)
            weight = np.exp(-step * step / (2 * sigma**2)) * active
            total += _sample(response * alpha, y, x) * weight
            weight_sum += _sample(alpha, y, x) * weight
    return cast(
        Array,
        np.divide(total, weight_sum, out=response.copy(), where=weight_sum > 1e-12),
    )


def flow_line(
    image: Image.Image,
    *,
    derivative_sigma: float = 1,
    etf_radius: int = 3,
    etf_iterations: int = 3,
    sigma_normal: float = 1,
    gaussian_ratio: float = 1.6,
    sigma_tangent: float = 3,
    tau: float = 0.99,
    threshold: float = 0,
    strength: float = 0.3,
    output: str = "overlay",
    border: str = "reflect",
) -> Image.Image:
    """ETF/FDoG with normal Gaussian difference and tangent streamline integration.

    Lines are black on white; output='lines' returns the independent line map.
    strength blends only overlay output. strength=0 always returns the input.
    threshold offsets normalized signed FDoG; no global percentile normalization.
    """
    for name, value, low, high in (
        ("derivative_sigma", derivative_sigma, 0.3, 4),
        ("sigma_normal", sigma_normal, 0.3, 4),
        ("gaussian_ratio", gaussian_ratio, 1.01, 3),
        ("sigma_tangent", sigma_tangent, 0.5, 12),
        ("tau", tau, 0.8, 1.1),
        ("threshold", threshold, -1, 1),
        ("strength", strength, 0, 1),
    ):
        _number(name, value, low, high)
    _number("etf_radius", etf_radius, 1, 8, integer=True)
    _number("etf_iterations", etf_iterations, 0, 8, integer=True)
    if output not in {"overlay", "lines"} or border != "reflect":
        raise ValueError("flow_line requires overlay/lines output and reflect border")
    source, alpha = _input(image)
    if strength == 0:
        return image.copy()
    guide = _weighted_gaussian(_luma(source), alpha, derivative_sigma)
    tangent, magnitude = _etf(guide, alpha, etf_radius, etf_iterations)
    response = _normal_dog(_luma(source), alpha, tangent, sigma_normal, gaussian_ratio, tau)
    response = _flow_integrate(response, alpha, tangent, sigma_tangent)
    # Flat, directionless regions have no evidence for a contour.
    lines = np.where(
        (response >= threshold) | (magnitude < 1e-8), 1, 1 + np.tanh(10 * (response - threshold))
    )
    result = np.repeat(lines[..., None], 3, axis=2)
    if output == "overlay":
        result = source * ((1 - strength) + strength * result)
    return _output(result, image, alpha)


def kuwahara_anisotropic(
    image: Image.Image, *, radius: int = 3, sectors: int = 8, strength: float = 1.0
) -> Image.Image:
    """Tensor-oriented elliptical Kuwahara with overlapping directional sectors.

    The alpha=1 tensor ellipse has semiaxes radius*(1+A) and radius/(1+A).
    Smooth cosine sector masks are a documented discretization variant; sector
    RGB means and variances are coverage weighted, with inverse-variance mixing.
    """
    _number("radius", radius, 1, 16, integer=True)
    if (
        sectors not in {4, 8, 12, 16}
        or isinstance(sectors, bool)
        or not isinstance(sectors, Integral)
    ):
        raise ValueError("sectors must be 4, 8, 12 or 16")
    _number("strength", strength, 0, 1)
    source, alpha = _input(image)
    if strength == 0:
        return image.copy()
    smoothed = _weighted_gaussian(source, alpha, 1)
    gy, gx = _gradient(smoothed)
    e = _weighted_gaussian(np.sum(gx * gx, axis=2), alpha, 2)
    f = _weighted_gaussian(np.sum(gx * gy, axis=2), alpha, 2)
    g = _weighted_gaussian(np.sum(gy * gy, axis=2), alpha, 2)
    anisotropy = np.sqrt((e - g) ** 2 + 4 * f**2) / np.maximum(e + g, 1e-12)
    angle = 0.5 * np.arctan2(2 * f, e - g) + np.pi / 2
    cs, sn = np.cos(angle), np.sin(angle)
    major, minor = radius * (1 + anisotropy), radius / (1 + anisotropy)
    yy, xx = np.mgrid[: alpha.shape[0], : alpha.shape[1]]
    # Offset-major traversal reuses geometry and integer reflect samples without
    # caching full images per offset; each sector retains its accumulation order.
    totals = [np.zeros_like(source) for _ in range(sectors)]
    square_totals = [np.zeros_like(source) for _ in range(sectors)]
    counts = [np.zeros_like(alpha) for _ in range(sectors)]
    for dy in range(-2 * radius, 2 * radius + 1):
        for dx in range(-2 * radius, 2 * radius + 1):
            yi = _reflect((yy + dy).astype(float), source.shape[0]).astype(int)
            xi = _reflect((xx + dx).astype(float), source.shape[1]).astype(int)
            values, coverage = source[yi, xi], alpha[yi, xi]
            squared = values**2
            u, v = (dx * cs + dy * sn) / major, (-dx * sn + dy * cs) / minor
            distance = u * u + v * v
            theta = np.arctan2(v, u)
            radial = np.exp(-2 * distance) * (distance <= 1)
            for sector in range(sectors):
                angular = np.maximum(0, np.cos(theta - sector * 2 * np.pi / sectors)) ** (
                    sectors / 2
                )
                if dx == 0 and dy == 0:
                    angular = np.ones_like(alpha)
                weight = angular * radial * coverage
                totals[sector] += weight[..., None] * values
                square_totals[sector] += weight[..., None] * squared
                counts[sector] += weight
    output, total_weight = np.zeros_like(source), np.zeros_like(alpha)
    for total, squares, count in zip(totals, square_totals, counts, strict=True):
        mean = np.divide(total, count[..., None], out=source.copy(), where=count[..., None] > 1e-12)
        variance = np.maximum(0, squares / np.maximum(count[..., None], 1e-12) - mean**2).sum(
            axis=2
        )
        weight = (count > 1e-12) / (1 + (255**2 * variance) ** 2)
        output += mean * weight[..., None]
        total_weight += weight
    filtered = np.divide(
        output, total_weight[..., None], out=source.copy(), where=total_weight[..., None] > 1e-12
    )
    return _output(source * (1 - strength) + filtered * strength, image, alpha)


def weighted_guided_smooth(
    image: Image.Image, *, radius: int = 4, epsilon: float = 0.001,
    strength: float = 0.5,
) -> Image.Image:
    """Li et al. (2015), equations 5-10; luma/coverage reference variant.

    Gaussian smoothing of variance weights uses sigma=1, reflect boundaries.
    Coverage-weighted global normalization excludes hidden RGB.
    """
    _number("radius", radius, 1, 128, integer=True)
    _number("epsilon", epsilon, 1e-6, 1)
    _number("strength", strength, 0, 1)
    source, alpha = _input(image)
    if strength == 0 or alpha.sum() == 0:
        return image.copy()
    guide = _luma(source)
    small_mean = _average(guide, alpha, 1)
    variance = np.maximum(0, _average(guide * guide, alpha, 1) - small_mean**2)
    stabilizer = 0.001**2
    normalizer = (alpha / (variance + stabilizer)).sum() / alpha.sum()
    weights = _weighted_gaussian((variance + stabilizer) * normalizer, alpha, 1)
    mean_g = _average(guide, alpha, radius)
    mean_p = _average(source, alpha, radius)
    covariance = _average(guide[..., None] * source, alpha, radius) - mean_g[..., None] * mean_p
    guide_variance = np.maximum(0, _average(guide * guide, alpha, radius) - mean_g**2)
    a = covariance / (guide_variance + epsilon / np.maximum(weights, 1e-12))[..., None]
    b = mean_p - a * mean_g[..., None]
    filtered = _average(a, alpha, radius) * guide[..., None] + _average(b, alpha, radius)
    return _output(source * (1 - strength) + filtered * strength, image, alpha)


def local_laplacian_sampled(
    image: Image.Image, *, sigma: float = 0.2, detail: float = 2,
    samples: int = 8, levels: int = 5, strength: float = 0.5,
) -> Image.Image:
    """Paris et al. remapping, uniformly sampled reference-value approximation.

    Not the original per-coefficient algorithm: Gaussian sigma=1 reduction,
    bilinear expansion and linear interpolation between sampled remapped pyramids.
    Hidden RGB uses visible-mean extension, not masked pyramid convolution.
    """
    from imagen_cli.application.filters_fast import _resize
    from imagen_cli.application.filters_structure import _input as structure_input

    _number("sigma", sigma, 0.01, 1)
    _number("detail", detail, 0.25, 4)
    _number("samples", samples, 4, 32, integer=True)
    _number("levels", levels, 1, 8, integer=True)
    _number("strength", strength, 0, 1)
    source, alpha = _input(image)
    if strength == 0 or detail == 1 or alpha.sum() == 0:
        return image.copy()
    extended = structure_input(image, strength)
    guide = _luma(extended)

    def pyramid(values: Array) -> list[Array]:
        result = [values]
        for _ in range(levels):
            if min(result[-1].shape) <= 1:
                break
            result.append(_gaussian(result[-1], 1)[::2, ::2])
        return result

    base = pyramid(guide)
    bands = [np.zeros_like(layer) for layer in base[:-1]]
    spacing = 1 / (samples - 1)
    for reference in np.linspace(0, 1, samples):
        delta = guide - reference
        distance = np.abs(delta)
        remapped = reference + np.sign(delta) * np.where(
            distance <= sigma, sigma * (distance / sigma)**detail, distance,
        )
        layers = pyramid(remapped)
        for index, band in enumerate(bands):
            size = (band.shape[1], band.shape[0])
            residual = layers[index] - _resize(layers[index + 1], size)
            weight = np.maximum(0, 1 - np.abs(base[index] - reference) / spacing)
            band += residual * weight
    target = base[-1]
    for band in reversed(bands):
        target = band + _resize(target, (band.shape[1], band.shape[0]))
    # Additive encoded-luma adjustment; final gamut clipping is explicit.
    filtered = extended + (target - guide)[..., None]
    return _output(source * (1 - strength) + filtered * strength, image, alpha)


OPS: dict[str, Callable[..., Image.Image]] = {
    "local_laplacian_sampled": local_laplacian_sampled,
    "weighted_guided_smooth": weighted_guided_smooth,
    "guided_smooth": guided_smooth,
    "rolling_guidance": rolling_guidance,
    "flow_line": flow_line,
    "kuwahara_anisotropic": kuwahara_anisotropic,
}
