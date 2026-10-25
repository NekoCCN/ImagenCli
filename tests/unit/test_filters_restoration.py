"""Numerical contracts for the explicit, experimental restoration variants."""

import importlib

import pytest
from PIL import Image

np = pytest.importorskip("numpy")


def module():
    # Import inside tests so RED reports the missing implementation as an assertion.
    assert importlib.util.find_spec("imagen_cli.application.filters_restoration") is not None
    return importlib.import_module("imagen_cli.application.filters_restoration")


def image(values):
    return Image.fromarray(np.asarray(values, dtype=np.uint8))


def patterned(mode="RGBA"):
    rng = np.random.default_rng(24)
    return image(rng.integers(0, 256, (15, 17, 4 if mode == "RGBA" else 3)))


CASES = {
    "bounded_sharpen": {"amount": 1.5, "radius": 1.2, "noise_gate": 0.01},
    "multiscale_detail": {"fine": 1.4, "medium": 0.7, "coarse": 1.1},
    "periodic_notch": {"frequencies": "0.25,0", "width": 0.02},
    "chroma_transfer": {"reference": Image.new("RGB", (3, 4), (190, 140, 120))},
    "illumination_correct": {"strength": 0.6, "radius": 3},
}


@pytest.mark.parametrize("name", CASES)
def test_alpha_determinism_ranges_and_no_mutation(name):
    source = patterned()
    before = source.tobytes()
    op = module().OPS[name]
    first = op(source, **CASES[name])
    second = op(source, **CASES[name])
    assert source.tobytes() == before
    assert first.tobytes() == second.tobytes()
    assert first.size == source.size
    assert first.mode == "RGBA"
    assert first.getchannel("A").tobytes() == source.getchannel("A").tobytes()
    assert np.isfinite(np.asarray(first)).all()


@pytest.mark.parametrize("name", ["bounded_sharpen", "multiscale_detail", "periodic_notch",
                                  "illumination_correct"])
def test_uniform_is_unchanged(name):
    source = Image.new("RGB", (11, 9), (81, 117, 155))
    assert module().OPS[name](source).tobytes() == source.tobytes()


@pytest.mark.parametrize(("name", "params"), [
    ("bounded_sharpen", {"amount": 0}),
    ("multiscale_detail", {"fine": 1, "medium": 1, "coarse": 1}),
    ("periodic_notch", {"frequencies": ""}),
    ("chroma_transfer", {"reference": Image.new("RGB", (1, 1)), "strength": 0}),
    ("illumination_correct", {"strength": 0}),
])
def test_exact_bypass(name, params):
    source = patterned()
    assert module().OPS[name](source, **params).tobytes() == source.tobytes()


def test_sharpen_noise_gate_and_local_bound():
    source = np.full((15, 32, 3), 90, dtype=np.uint8)
    source[:, 16:] = 170
    source[7, 6] += 1
    original = image(source)
    result = np.asarray(module().bounded_sharpen(
        original, amount=4, radius=1, noise_gate=0.02, overshoot=0))
    assert result.min() >= 90
    assert result.max() <= 170
    assert result[7, 6, 0] == 91
    # A smooth, non-saturated edge must actually be enhanced (not an identity stub).
    ramp = np.tile(np.array([80, 80, 90, 110, 150, 170, 180, 180]), (9, 1))
    ramp = image(np.repeat(ramp[..., None], 3, axis=2))
    assert module().bounded_sharpen(ramp, noise_gate=0).tobytes() != ramp.tobytes()


def test_multiscale_has_real_independent_band_controls():
    source = patterned("RGB")
    fine = module().multiscale_detail(source, fine=0, medium=1, coarse=1)
    coarse = module().multiscale_detail(source, fine=1, medium=1, coarse=0)
    assert fine.tobytes() != source.tobytes()
    assert coarse.tobytes() != fine.tobytes()
    array = np.asarray(source, dtype=float) / 255
    bands = module()._detail_bands(array, np.ones(array.shape[:2]), 1.0)
    assert np.max(np.abs(sum(bands) - array)) < 1e-12


def test_notch_attenuates_only_selected_conjugate_frequency_and_preserves_dc():
    x = np.arange(128)
    wave = np.rint(128 + 24 * np.sin(2 * np.pi * x / 8)
                   + 18 * np.sin(2 * np.pi * x / 16)).astype(np.uint8)
    source = image(np.tile(wave[None, :, None], (64, 1, 3)))
    op = module().periodic_notch
    out = op(source, frequencies="0.125,0", width=0.008, strength=1)
    before = np.abs(np.fft.rfft(np.asarray(source)[0, :, 0].astype(float)))
    after = np.abs(np.fft.rfft(np.asarray(out)[0, :, 0].astype(float)))
    assert after[16] < before[16] * 0.04
    assert after[8] == pytest.approx(before[8], rel=0.04)
    assert after[0] == pytest.approx(before[0], abs=128)
    assert out.tobytes() == op(source, frequencies="-0.125,0", width=0.008).tobytes()


def test_chroma_transfer_changes_chroma_preserving_linear_luminance(tmp_path):
    source = Image.new("RGBA", (4, 4), (130, 130, 130, 73))
    reference = Image.new("RGB", (5, 3), (160, 100, 90))
    path = tmp_path / "reference.png"
    reference.save(path)
    out = module().chroma_transfer(source, reference=str(path), luminance="preserve")
    rgb = np.asarray(out)[..., :3] / 255
    original = np.asarray(source)[..., :3] / 255
    def decode(x):
        return np.where(x <= .04045, x / 12.92, ((x + .055) / 1.055) ** 2.4)
    weights = np.array([.2126, .7152, .0722])
    assert np.max(np.abs(decode(rgb) @ weights - decode(original) @ weights)) < .004
    assert out.getpixel((0, 0))[0] > out.getpixel((0, 0))[2]
    assert module().chroma_transfer(source, reference=reference,
                                    luminance="match").tobytes() != out.tobytes()


@pytest.mark.parametrize("name", CASES)
def test_hidden_rgb_does_not_contaminate_visible_output(name):
    a = np.full((15, 17, 4), 100, dtype=np.uint8)
    a[..., 3] = 255
    a[:, :5, 3] = 0
    b = a.copy()
    a[:, :5, :3] = [255, 0, 255]
    b[:, :5, :3] = [0, 255, 0]
    first = np.asarray(module().OPS[name](image(a), **CASES[name]))
    second = np.asarray(module().OPS[name](image(b), **CASES[name]))
    assert np.array_equal(first[:, 5:], second[:, 5:])


def test_linear_resize_averages_light_and_premultiplies_alpha():
    op = module().linear_resize
    bw = image([[[0, 0, 0], [255, 255, 255]]])
    assert op(bw, width=1, height=1, filter="box").getpixel((0, 0)) == (188, 188, 188)
    transparent = image([[[255, 0, 255, 0], [0, 255, 0, 255]]])
    assert op(transparent, width=1, height=1, filter="box").getpixel((0, 0)) == (0, 255, 0, 128)
    assert op(Image.new("RGBA", (3, 2), (255, 0, 0, 0)), scale=2).getbbox() is None
    assert op(patterned(), scale=1).tobytes() == patterned().tobytes()


def test_illumination_correction_reduces_smooth_shading():
    gray = np.tile(np.linspace(40, 170, 64), (16, 1))
    source = image(np.repeat(gray[..., None], 3, axis=2))
    result = module().illumination_correct(source, radius=8, strength=1)
    assert np.std(np.asarray(result)[..., 0]) < np.std(np.asarray(source)[..., 0]) * .7


@pytest.mark.parametrize(("name", "params"), [
    ("bounded_sharpen", {"amount": float("nan")}),
    ("bounded_sharpen", {"radius": 0}),
    ("bounded_sharpen", {"noise_gate": -1}),
    ("bounded_sharpen", {"overshoot": 1.1}),
    ("multiscale_detail", {"fine": -1}),
    ("multiscale_detail", {"radius": float("inf")}),
    ("periodic_notch", {"frequencies": "0,0"}),
    ("periodic_notch", {"frequencies": "0.6,0"}),
    ("periodic_notch", {"frequencies": "nan,0"}),
    ("periodic_notch", {"frequencies": "0.1"}),
    ("periodic_notch", {"width": 0}),
    ("periodic_notch", {"strength": float("inf")}),
    ("chroma_transfer", {"reference": Image.new("RGB", (1, 1)), "luminance": "Lab"}),
    ("chroma_transfer", {"reference": Image.new("RGBA", (1, 1), (0, 0, 0, 0))}),
    ("linear_resize", {"width": 0}),
    ("linear_resize", {"scale": float("nan")}),
    ("linear_resize", {"width": 2.5}),
    ("linear_resize", {"width": 2, "filter": "unknown"}),
    ("illumination_correct", {"floor": 0}),
    ("illumination_correct", {"strength": -1}),
])
def test_invalid_parameters_rejected(name, params):
    with pytest.raises(ValueError):
        module().OPS[name](patterned(), **params)
