"""Optional numpy filters: present, repeatable, and metrically sane."""

import pytest

pytest.importorskip("numpy")

from PIL import Image

from imagen_cli.application import filters_numpy, media_ops


@pytest.fixture
def image() -> Image.Image:
    source = Image.new("RGB", (16, 16))
    for y in range(16):
        for x in range(16):
            source.putpixel((x, y), ((x * 15) % 256, (y * 15) % 256, ((x * y) % 16) * 16))
    return source


CASES = {
    "kuwahara": {"size": 3},
    "bilateral": {"radius": 2, "sigma_color": 25, "sigma_space": 2},
    "guided": {"radius": 2},
    "nl_means": {"strength": 10, "patch": 1, "search": 1},
    "total_variation": {"iterations": 3},
    "clahe": {"tiles": 4, "clip": 2},
    "curves": {"points": "0,0;128,100;255,255"},
    "dehaze": {"radius": 3},
    "deskew": {"limit": 4},
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_filter_runs_and_is_repeatable(image, name):
    first = media_ops.OPS[name](image.copy(), **CASES[name])
    second = media_ops.OPS[name](image.copy(), **CASES[name])
    assert first.size == second.size == image.size
    assert first.tobytes() == second.tobytes()


def test_quality_metrics(image):
    same = image.copy()
    assert filters_numpy.psnr(image, same) == float("inf")
    assert filters_numpy.ssim(image, same) == pytest.approx(1.0)
    darker = image.point(lambda value: value // 2)
    assert 0 < filters_numpy.psnr(image, darker) < 30
    assert filters_numpy.ssim(image, darker) < 1.0


def test_cube_lut_round_trip(image, tmp_path):
    lines = ["LUT_3D_SIZE 2"]
    for blue in (0, 1):
        for green in (0, 1):
            for red in (0, 1):
                lines.append(f"{red} {green} {blue}")
    path = tmp_path / "identity.cube"
    path.write_text("\n".join(lines))
    applied = media_ops.OPS["cube_lut"](image, path=str(path))
    assert applied.size == image.size
    assert applied.tobytes() == image.tobytes()


def test_bad_parameters_are_refused(image):
    with pytest.raises(ValueError, match="kuwahara"):
        media_ops.OPS["kuwahara"](image, size=4)
    with pytest.raises(ValueError, match="CLAHE"):
        media_ops.OPS["clahe"](image, tiles=1)
