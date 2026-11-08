"""The fp16 tolerance: half precision must not visibly move the alpha edge.

Measured on an RTX 2050 (4 GB): fp32 cannot allocate at the graph's 1024x1024
input (822 MB request), so the reference has to come from the CPU. Measured
agreement for this fixture — max 3/255, mean 0.034/255 — sets the guard below.

These tests need a real CUDA provider plus the pinned weights; ``conftest.py``
skips them where no CUDA provider exists.
"""

import numpy as np
import pytest
from PIL import Image

from imagen_cli.application import matting

pytestmark = pytest.mark.cuda

# Design doc §6.3 tolerance, in 1/255 units, with headroom over the measurement.
MAX_ALPHA_DELTA = 6
MEAN_ALPHA_DELTA = 1.0


def _subject(size=(96, 96)):
    """A hard edge plus a soft gradient: an edge is where fp16 would show."""
    image = Image.new("RGB", size)
    rows = []
    for y in range(size[1]):
        for x in range(size[0]):
            inside = x > size[0] // 2
            rows.append((240, 230, 220) if inside else (y * 2 % 256, 40, 60))
    image.putdata(rows)
    return image


def test_fp16_alpha_stays_within_the_published_tolerance():
    image = _subject()
    reference, _ = matting.matte(image, precision="fp32", backend="cpu")
    derived, engine = matting.matte(image, precision="fp16", backend="cuda")

    assert engine["precision"] == "fp16"
    assert engine["provider"] == "CUDAExecutionProvider"

    before = np.asarray(reference.getchannel("A"), dtype=np.int16)
    after = np.asarray(derived.getchannel("A"), dtype=np.int16)
    assert before.shape == after.shape
    delta = np.abs(before - after)
    assert delta.max() <= MAX_ALPHA_DELTA, (
        f"max alpha delta {delta.max()} exceeds {MAX_ALPHA_DELTA}"
    )
    assert delta.mean() <= MEAN_ALPHA_DELTA, (
        f"mean alpha delta {delta.mean()} exceeds {MEAN_ALPHA_DELTA}"
    )


def test_fp16_derivation_is_cached_for_the_next_run():
    from imagen_cli.application import matting_fp16

    assert matting_fp16.derived_path("birefnet-lite").is_file()
    fingerprint = matting_fp16.fingerprint_path("birefnet-lite")
    assert fingerprint.is_file()
    _, info = matting_fp16.ensure_fp16("birefnet-lite")
    assert info["derived"] is False
