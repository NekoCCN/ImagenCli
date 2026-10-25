"""Edge-preserving WLS scale bands are distinct from Gaussian detail bands."""

import numpy as np
import pytest
from PIL import Image

from imagen_cli.application import media_ops, postprocess_service
from imagen_cli.application.operator_catalog import get_operator_item


def test_unity_gains_reconstruct_exactly():
    image = Image.fromarray(np.random.default_rng(3).integers(0, 256, (13, 17, 4), dtype=np.uint8))
    assert media_ops.OPS["wls_detail"](image).tobytes() == image.tobytes()


def test_zero_detail_gains_reduce_texture_and_preserve_edge():
    pixels = np.full((16, 32, 3), 80, dtype=np.uint8)
    pixels[:, 16:] = 180
    pixels[::2, ::2] += 4
    result = np.asarray(media_ops.OPS["wls_detail"](
        Image.fromarray(pixels), fine=0, medium=0, coarse=0,
    )).astype(float)
    assert result[4:12, 4:12].var() < pixels[4:12, 4:12].var()
    assert result[:, 20:].mean() - result[:, :12].mean() > 80


def test_alpha_and_hidden_rgb_preserved():
    pixels = np.full((8, 8, 4), (100, 120, 140, 128), dtype=np.uint8)
    pixels[:, :3] = (255, 0, 250, 0)
    image = Image.fromarray(pixels)
    result = np.asarray(media_ops.OPS["wls_detail"](image, fine=0.5))
    np.testing.assert_array_equal(result[..., 3], pixels[..., 3])
    np.testing.assert_array_equal(result[:, :3], pixels[:, :3])


@pytest.mark.parametrize("params", [{"fine": -1}, {"smoothing": 0},
    {"scale_ratio": 1}, {"alpha": float("nan")}])
def test_invalid_parameters_rejected(params):
    with pytest.raises(ValueError):
        media_ops.OPS["wls_detail"](Image.new("RGB", (5, 5)), **params)


def test_shared_recipe_and_numerical_provenance():
    item = get_operator_item("wls_detail")
    assert item is not None
    step = postprocess_service.resolve_steps(item.build_pipeline())[0]
    assert step["border"] == "natural"
    assert step["approximation"] == "fixed_guide_wls_scale_bands"
