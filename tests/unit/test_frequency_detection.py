"""Narrow periodic peak detection is conservative and reports selected frequencies."""

import numpy as np
import pytest
from PIL import Image

from imagen_cli.application import media_ops
from imagen_cli.application.operator_catalog import get_operator_item


def test_detects_known_conjugate_peak_and_reduces_paired_error():
    from imagen_cli.application.filters_frequency import detect_periodic_peaks

    x = np.arange(64)
    noisy = np.rint(128 + 20 * np.cos(2 * np.pi * x / 8)).astype(np.uint8)
    pixels = np.tile(noisy[None, :, None], (64, 1, 3))
    image = Image.fromarray(pixels)
    peaks = detect_periodic_peaks(image)
    assert any(abs(fx - 0.125) < 1e-9 and abs(fy) < 1e-9 for fx, fy in peaks)
    output = np.asarray(media_ops.OPS["periodic_notch_auto"](image, strength=1)).astype(float)
    assert np.abs(output - 128).mean() < np.abs(pixels.astype(float) - 128).mean() / 2


def test_constant_has_no_peaks_and_is_byte_exact():
    from imagen_cli.application.filters_frequency import detect_periodic_peaks

    image = Image.new("RGBA", (64, 64), (90, 120, 140, 128))
    assert detect_periodic_peaks(image) == []
    assert media_ops.OPS["periodic_notch_auto"](image).tobytes() == image.tobytes()


def test_smooth_gradient_is_not_periodic_noise():
    from imagen_cli.application.filters_frequency import detect_periodic_peaks

    ramp = np.tile(np.arange(64, dtype=np.uint8)[None, :, None] * 3, (64, 1, 3))
    assert detect_periodic_peaks(Image.fromarray(ramp)) == []


def test_hidden_rgb_has_no_detectable_spectrum():
    from imagen_cli.application.filters_frequency import detect_periodic_peaks

    pixels = np.random.default_rng(3).integers(0, 256, (64, 64, 4), dtype=np.uint8)
    pixels[..., 3] = 0
    image = Image.fromarray(pixels)
    assert detect_periodic_peaks(image) == []
    assert media_ops.OPS["periodic_notch_auto"](image).tobytes() == image.tobytes()


@pytest.mark.parametrize("params", [{"threshold": 0}, {"max_pairs": True},
    {"min_frequency": 0}, {"strength": float("nan")}])
def test_invalid_detection_parameters_rejected(params):
    with pytest.raises(ValueError):
        media_ops.OPS["periodic_notch_auto"](Image.new("RGB", (32, 32)), **params)


def test_auto_filter_is_explicit_opt_in_recipe():
    item = get_operator_item("periodic_notch_auto")
    assert item is not None
    assert item.build_pipeline()[0]["op"] == "periodic_notch_auto"
    assert "有意" in item.description


def test_intentional_stripes_are_an_explicit_negative_example():
    x = np.arange(64)
    stripes = np.tile(np.rint(128 + 20 * np.cos(2 * np.pi * x / 8))[None, :, None],
                      (64, 1, 3)).astype(np.uint8)
    image = Image.fromarray(stripes)
    result = media_ops.OPS["periodic_notch_auto"](image)
    # Periodicity alone cannot establish that the author's pattern is unwanted.
    assert result.tobytes() != image.tobytes()
    assert result.info["postprocess_detection"]["semantic_confidence"] == "unknown"


def test_render_records_selected_frequencies(tmp_path):
    from imagen_cli.application.postprocess_service import render

    x = np.arange(64)
    pixels = np.tile(np.rint(128 + 20 * np.cos(2 * np.pi * x / 8))[None, :, None],
                     (64, 1, 3)).astype(np.uint8)
    path = tmp_path / "periodic.png"
    Image.fromarray(pixels).save(path)
    _, metadata = render(path, [{"op": "periodic_notch_auto"}])
    assert metadata["detections"][0]["step"] == 0
    assert [0.125, 0.0] in metadata["detections"][0]["frequencies"]
