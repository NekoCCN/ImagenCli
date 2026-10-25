"""Reject oversized work before allocating output pixels."""

import pytest
from PIL import Image

from imagen_cli.application import postprocess_service


def test_input_pixel_budget_rejects_before_processing():
    with pytest.raises(ValueError, match="input pixel budget"):
        postprocess_service.check_budget((8000, 8000), [{"op": "brightness", "params": {}}])


def test_resize_output_budget_is_checked_without_allocation():
    with pytest.raises(ValueError, match="output pixel budget"):
        postprocess_service.check_budget((64, 64), [
            {"op": "linear_resize", "params": {"scale": 1000}},
        ])


def test_budget_tracks_intermediate_dimensions():
    with pytest.raises(ValueError, match="output pixel budget"):
        postprocess_service.check_budget((1000, 1000), [
            {"op": "linear_resize", "params": {"scale": 4}},
            {"op": "linear_resize", "params": {"scale": 4}},
        ])


@pytest.mark.parametrize("operation", ["weighted_guided_smooth", "local_laplacian_sampled"])
def test_new_pyramid_and_weighted_filters_preflight_buffers(operation):
    with pytest.raises(ValueError, match="working memory"):
        postprocess_service.check_budget((3000, 3000), [{"op": operation, "params": {}}])


def test_small_pipeline_budget_is_accepted():
    postprocess_service.check_budget(Image.new("RGB", (10, 10)).size, [
        {"op": "brightness", "params": {"factor": 1}},
    ])


@pytest.mark.parametrize("step", [
    {"op": "pad", "params": {"width": 100000, "height": 100000}},
    {"op": "crop", "params": {"right": 100000, "bottom": 100000}},
    {"op": "superres", "params": {"model": "realesrgan-x4plus"}},
    {"op": "block_scale", "params": {"factor": 4}},
    {"op": "perspective", "params": {"width": 100000, "height": 100000}},
])
def test_other_expanding_operations_are_checked(step):
    with pytest.raises(ValueError, match="output pixel budget"):
        postprocess_service.check_budget((3000, 3000), [step])


@pytest.mark.parametrize("params", [
    {"scale": "large"}, {"scale": True}, {"width": -1},
    {"height": 0}, {"width": 1.5}, {"scale": 0.000001},
    {"scale": 2, "width": 10},
])
def test_invalid_resize_dimensions_raise_value_error(params):
    with pytest.raises(ValueError, match="dimension|scale|mutually"):
        postprocess_service.check_budget((10, 10), [{"op": "linear_resize", "params": params}])


def test_crop_and_rotation_are_tracked_before_resize():
    postprocess_service.check_budget((3000, 3000), [
        {"op": "crop", "params": {"right": 10, "bottom": 20}},
        {"op": "rotate", "params": {"degrees": 90}},
        {"op": "linear_resize", "params": {"width": 5000}},
    ])


def test_kuwahara_sector_buffers_are_included_in_memory_budget():
    with pytest.raises(ValueError, match="working memory budget"):
        postprocess_service.check_budget((4000, 4000), [
            {"op": "kuwahara_anisotropic", "params": {"sectors": 16}},
        ])


def test_global_fft_working_buffers_are_not_just_input_bytes():
    with pytest.raises(ValueError, match="working memory budget"):
        postprocess_service.check_budget((5000, 5000), [{"op": "l0_smooth"}])


def test_linear_resize_output_buffers_are_included():
    with pytest.raises(ValueError, match="working memory budget"):
        postprocess_service.check_budget((10, 10), [
            {"op": "linear_resize", "params": {"width": 8000, "height": 8000}},
        ])


def test_rank_filter_wide_row_temporary_budget():
    with pytest.raises(ValueError, match="working memory budget"):
        postprocess_service.check_budget((100000, 1), [
            {"op": "weighted_median", "params": {"radius": 8}},
        ])
