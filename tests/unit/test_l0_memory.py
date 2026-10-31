"""L0 smoothing must match the textbook solver without full-size float64 buffers."""

import json
import subprocess
import sys
import textwrap

import numpy as np
import pytest
from PIL import Image

from imagen_cli.application import media_ops


def _reference(pixels: np.ndarray, smoothing: float, beta_growth: float) -> np.ndarray:
    """The paper's half-quadratic solver in float64: the numerical contract."""
    original = pixels.astype(np.float64) / 255
    height, width = original.shape[:2]
    frequencies = (4 * np.sin(np.pi * np.fft.fftfreq(height))[:, None] ** 2
                   + 4 * np.sin(np.pi * np.fft.fftfreq(width))[None, :] ** 2)
    numerator = np.fft.fft2(original, axes=(0, 1))
    result = original.copy()
    beta = 2 * smoothing
    while beta < 1e5:
        dx = np.roll(result, -1, axis=1) - result
        dy = np.roll(result, -1, axis=0) - result
        keep = np.sum(dx * dx + dy * dy, axis=2) > smoothing / beta
        dx *= keep[..., None]
        dy *= keep[..., None]
        divergence = np.roll(dx, 1, axis=1) - dx + np.roll(dy, 1, axis=0) - dy
        result = np.fft.ifft2(
            (numerator + beta * np.fft.fft2(divergence, axes=(0, 1)))
            / (1 + beta * frequencies[..., None]), axes=(0, 1)
        ).real
        beta *= beta_growth
    return np.rint(np.clip(result, 0, 1) * 255).astype(np.uint8)


@pytest.mark.parametrize(("smoothing", "beta_growth"), [(0.02, 2.0), (0.005, 1.5)])
def test_l0_matches_the_float64_reference(smoothing: float, beta_growth: float):
    rng = np.random.default_rng(7)
    array = rng.integers(0, 256, (48, 64, 3), dtype=np.uint8)
    array[16:32, 16:48] = 128
    expected = _reference(array, smoothing, beta_growth)
    actual = np.asarray(media_ops.OPS["l0_smooth"](
        Image.fromarray(array), smoothing=smoothing, beta_growth=beta_growth))
    assert np.max(np.abs(actual.astype(int) - expected.astype(int))) <= 2


@pytest.mark.skipif(sys.platform != "linux", reason="reads VmHWM from /proc")
def test_l0_peak_memory_stays_a_small_multiple_of_pixels():
    script = textwrap.dedent(
        """
        import json
        import numpy as np
        from PIL import Image
        from imagen_cli.application import media_ops

        def peak():
            for line in open("/proc/self/status"):
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) * 1024
            raise SystemExit("no VmHWM")

        side = 1024
        image = Image.fromarray(np.zeros((side, side, 3), np.uint8))
        before = peak()
        media_ops.OPS["l0_smooth"](image)
        print(json.dumps({"bytes_per_pixel": (peak() - before) / (side * side)}))
        """
    )
    finished = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=300, check=True
    )
    measured = json.loads(finished.stdout)["bytes_per_pixel"]
    assert measured < 160, f"peak working set is {measured:.0f} bytes per pixel"
