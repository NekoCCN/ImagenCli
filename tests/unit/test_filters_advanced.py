"""Tests for the optional advanced NumPy postprocessing filters."""

import importlib
import subprocess
import sys

import pytest
from PIL import Image

np = pytest.importorskip("numpy")

from imagen_cli.application.filters_advanced import (  # noqa: E402
    OPS,
    flow_line,
    guided_smooth,
    kuwahara_anisotropic,
    rolling_guidance,
)


def rgba_fixture() -> Image.Image:
    pixels = np.zeros((15, 17, 4), dtype=np.uint8)
    pixels[..., :3] = (80, 120, 180)
    pixels[4:11, 7:10, :3] = (240, 240, 240)
    pixels[..., 3] = np.arange(15 * 17, dtype=np.uint8).reshape(15, 17)
    return Image.fromarray(pixels)


def test_ops_are_exported_and_strength_zero_is_exact_bypass():
    source = rgba_fixture()
    assert {"guided_smooth", "rolling_guidance", "flow_line", "kuwahara_anisotropic",
            "weighted_guided_smooth", "local_laplacian_sampled"} <= set(OPS)
    for operation in OPS.values():
        output = operation(source, strength=0)
        assert output is not source
        assert output.mode == source.mode
        assert output.tobytes() == source.tobytes()


@pytest.mark.parametrize("operation", OPS.values())
def test_filters_preserve_alpha_dimensions_and_are_repeatable(operation):
    source = rgba_fixture()
    output = operation(source)
    assert output.size == source.size
    assert output.mode == "RGBA"
    np.testing.assert_array_equal(np.asarray(output.getchannel("A")), source.getchannel("A"))
    assert operation(source).tobytes() == output.tobytes()


@pytest.mark.parametrize("operation", OPS.values())
@pytest.mark.parametrize("size", [(9, 11), (1, 1), (1, 7), (7, 1)])
def test_uniform_images_and_singleton_axes(operation, size):
    source = Image.new("RGB", size, (37, 91, 143))
    result = operation(source)
    np.testing.assert_allclose(np.asarray(result), np.asarray(source), atol=1)
    if operation == flow_line:
        assert np.asarray(operation(source, output="lines")).min() == 255


@pytest.mark.parametrize("operation", OPS.values())
def test_hidden_rgb_does_not_pollute_visible_pixels(operation):
    pixels = np.zeros((11, 11, 4), dtype=np.uint8)
    pixels[3:8, 3:8] = (60, 120, 180, 255)
    first = Image.fromarray(pixels)
    pixels[pixels[..., 3] == 0, :3] = (255, 0, 255)
    second = Image.fromarray(pixels)
    a, b = np.asarray(operation(first)), np.asarray(operation(second))
    mask = pixels[..., 3] > 0
    np.testing.assert_array_equal(a[mask], b[mask])
    np.testing.assert_array_equal(b[~mask], pixels[~mask])


def _naive_mean(values, radius):
    padded = np.pad(
        values, ((radius, radius), (radius, radius)) + ((0, 0),) * (values.ndim - 2), mode="reflect"
    )
    result = np.zeros_like(values)
    for y in range(values.shape[0]):
        for x in range(values.shape[1]):
            result[y, x] = padded[y : y + 2 * radius + 1, x : x + 2 * radius + 1].mean(axis=(0, 1))
    return result


def test_guided_matches_independent_local_linear_reference_and_rgb_guide():
    rng = np.random.default_rng(34)
    pixels = rng.integers(0, 256, (9, 8, 3), dtype=np.uint8)
    source = pixels.astype(float) / 255
    radius, epsilon = 2, 0.05
    for guidance in ("luma", "rgb"):
        guide = source @ np.array([0.299, 0.587, 0.114]) if guidance == "luma" else source
        if guidance == "luma":
            guide = guide[..., None]
        mg, mp = _naive_mean(guide, radius), _naive_mean(source, radius)
        cross = (
            _naive_mean(guide[..., :, None] * source[..., None, :], radius)
            - mg[..., :, None] * mp[..., None, :]
        )
        covariance = (
            _naive_mean(guide[..., :, None] * guide[..., None, :], radius)
            - mg[..., :, None] * mg[..., None, :]
        )
        a = np.empty_like(cross)
        for y in range(9):
            for x in range(8):
                a[y, x] = np.linalg.solve(
                    covariance[y, x] + epsilon * np.eye(guide.shape[-1]), cross[y, x]
                )
        b = mp - np.einsum("...ij,...i->...j", a, mg)
        reference = np.einsum("...ij,...i->...j", _naive_mean(a, radius), guide) + _naive_mean(
            b, radius
        )
        expected = np.clip(np.rint(reference * 255), 0, 255).astype(np.uint8)
        actual = guided_smooth(
            Image.fromarray(pixels), radius=radius, epsilon=epsilon, strength=1, guidance=guidance
        )
        np.testing.assert_array_equal(np.asarray(actual), expected)


def test_rolling_uses_gaussian_initialization_and_fixed_input_not_repeated_bilateral():
    # A single-pixel impulse should disappear under the initial scale, unlike
    # a range-preserving bilateral initialized directly from the original.
    pixels = np.full((17, 17, 3), 30, dtype=np.uint8)
    pixels[8, 8] = 240
    result = np.asarray(
        rolling_guidance(
            Image.fromarray(pixels), sigma_space=2, sigma_range=0.05, iterations=2, strength=1
        )
    )
    assert result[8, 8].max() < 80
    assert np.max(result) < 100


def test_guided_reduces_noise_without_blending_across_strong_edge():
    rng = np.random.default_rng(12)
    target = np.full((25, 31, 3), 40.0)
    target[:, 16:] = 210
    noisy = np.clip(target + rng.normal(0, 7, target.shape), 0, 255).astype(np.uint8)
    result = np.asarray(guided_smooth(Image.fromarray(noisy), radius=3, strength=1)).astype(float)
    assert np.mean((result - target) ** 2) < np.mean((noisy - target) ** 2)
    assert result[:, 15].mean() < 70
    assert result[:, 16].mean() > 180


def test_flow_line_detects_contours_and_has_grayscale_independent_output():
    pixels = np.zeros((25, 25, 3), dtype=np.uint8)
    pixels[12] = 255
    source = Image.fromarray(pixels)
    array = np.asarray(flow_line(source, output="lines", strength=1, etf_iterations=2))
    np.testing.assert_array_equal(array[..., 0], array[..., 1])
    assert array[9:16].min() < array[0].min()


def test_etf_aligns_antiparallel_directions_and_tangent_integration_follows_field():
    module = importlib.import_module("imagen_cli.application.filters_advanced")
    yy, xx = np.mgrid[:21, :21]
    field = np.stack((np.cos(xx / 8), np.sin(xx / 8)), axis=2)
    response = (yy == 10).astype(float)
    alpha = np.ones((21, 21))
    first = module._flow_integrate(response, alpha, field, 3)
    flipped = field.copy()
    flipped[::2] *= -1
    second = module._flow_integrate(response, alpha, flipped, 3)
    np.testing.assert_allclose(first, second, atol=1e-12)
    assert first[9].max() > 0


def test_kuwahara_changes_noisy_regions_and_respects_rotational_symmetry():
    rng = np.random.default_rng(12)
    pixels = rng.integers(30, 80, (15, 15, 3), dtype=np.uint8)
    pixels[:, 8:] += 150
    source = Image.fromarray(pixels)
    result = np.asarray(kuwahara_anisotropic(source, radius=2))
    assert result[:, :5].var() < pixels[:, :5].var()
    rotated = np.asarray(kuwahara_anisotropic(Image.fromarray(np.rot90(pixels)), radius=2))
    np.testing.assert_allclose(np.rot90(result), rotated, atol=1)


@pytest.mark.parametrize(
    "operation,kwargs",
    [
        (guided_smooth, {"radius": 0}),
        (guided_smooth, {"radius": 1.5}),
        (guided_smooth, {"epsilon": float("nan")}),
        (guided_smooth, {"guidance": "bad"}),
        (rolling_guidance, {"iterations": 0}),
        (rolling_guidance, {"iterations": 2.5}),
        (rolling_guidance, {"sigma_space": float("inf")}),
        (flow_line, {"output": "bad"}),
        (flow_line, {"etf_radius": True}),
        (flow_line, {"tau": float("nan")}),
        (flow_line, {"border": "wrap"}),
        (kuwahara_anisotropic, {"radius": 0}),
        (kuwahara_anisotropic, {"sectors": 7}),
        (kuwahara_anisotropic, {"sectors": 8.0}),
    ],
)
def test_invalid_parameters_rejected_even_on_bypass(operation, kwargs):
    with pytest.raises(ValueError):
        operation(Image.new("RGB", (3, 3)), strength=0, **kwargs)


@pytest.mark.parametrize("operation", OPS.values())
def test_nonfinite_strength_and_unsupported_mode_rejected(operation):
    with pytest.raises(ValueError):
        operation(Image.new("RGB", (3, 3)), strength=float("inf"))
    with pytest.raises(ValueError):
        operation(Image.new("L", (3, 3)))


@pytest.mark.parametrize("sectors", [4, 8, 12, 16])
@pytest.mark.parametrize("size", [(9, 7), (1, 5)])
def test_kuwahara_optimized_matches_sector_major_reference(sectors, size):
    module = importlib.import_module("imagen_cli.application.filters_advanced")
    rng = np.random.default_rng(42)
    image = Image.fromarray(rng.integers(0, 256, (*size, 4), dtype=np.uint8))
    source, alpha = module._input(image)
    radius = 2
    gy, gx = module._gradient(module._weighted_gaussian(source, alpha, 1))
    e, f, g = [
        module._weighted_gaussian(value, alpha, 2)
        for value in (np.sum(gx * gx, axis=2), np.sum(gx * gy, axis=2), np.sum(gy * gy, axis=2))
    ]
    anisotropy = np.sqrt((e - g) ** 2 + 4 * f**2) / np.maximum(e + g, 1e-12)
    angle = 0.5 * np.arctan2(2 * f, e - g) + np.pi / 2
    cs, sn = np.cos(angle), np.sin(angle)
    major, minor = radius * (1 + anisotropy), radius / (1 + anisotropy)
    yy, xx = np.mgrid[: alpha.shape[0], : alpha.shape[1]]
    result, denominator = np.zeros_like(source), np.zeros_like(alpha)
    for sector in range(sectors):
        total, squares, count = np.zeros_like(source), np.zeros_like(source), np.zeros_like(alpha)
        for dy in range(-2 * radius, 2 * radius + 1):
            for dx in range(-2 * radius, 2 * radius + 1):
                u, v = (dx * cs + dy * sn) / major, (-dx * sn + dy * cs) / minor
                distance = u * u + v * v
                theta = np.arctan2(v, u) - sector * 2 * np.pi / sectors
                angular = np.maximum(0, np.cos(theta)) ** (sectors / 2)
                if dx == 0 and dy == 0:
                    angular = np.ones_like(alpha)
                weight = angular * np.exp(-2 * distance) * (distance <= 1)
                weight *= module._sample(alpha, yy + dy, xx + dx)
                values = module._sample(source, yy + dy, xx + dx)
                total += weight[..., None] * values
                squares += weight[..., None] * values**2
                count += weight
        mean = np.divide(total, count[..., None], out=source.copy(), where=count[..., None] > 1e-12)
        variance = np.maximum(0, squares / np.maximum(count[..., None], 1e-12) - mean**2).sum(2)
        weight = (count > 1e-12) / (1 + (255**2 * variance) ** 2)
        result += mean * weight[..., None]
        denominator += weight
    expected = module._output(result / denominator[..., None], image, alpha)
    actual = kuwahara_anisotropic(image, radius=radius, sectors=sectors)
    assert actual.tobytes() == expected.tobytes()


def test_core_import_does_not_require_numpy():
    script = """
import sys
class Block:
    def find_spec(self, name, *args):
        if name == 'numpy' or name.startswith('numpy.'):
            raise ImportError('blocked optional numpy')
sys.meta_path.insert(0, Block())
import imagen_cli
import imagen_cli.application
assert 'numpy' not in sys.modules
"""
    subprocess.run([sys.executable, "-c", script], check=True)
