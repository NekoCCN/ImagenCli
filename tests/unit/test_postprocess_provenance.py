"""Effective parameter records remain replayable without changing legacy pipelines."""

import inspect

from imagen_cli.application import media_ops, postprocess_service


def test_resolved_steps_capture_defaults_and_implementation():
    steps = postprocess_service.resolve_steps([{"op": "brightness", "params": {}}])
    step = steps[0]
    assert step["algorithm_version"] == 1
    assert step["effective_params"]["factor"] == inspect.signature(
        media_ops.brightness
    ).parameters["factor"].default
    assert step["implementation"].endswith(".brightness")


def test_resolved_steps_keep_overrides():
    step = postprocess_service.resolve_steps([
        {"op": "brightness", "params": {"factor": 0.8}}
    ])[0]
    assert step["effective_params"]["factor"] == 0.8


def test_new_operator_records_numerical_contract():
    step = postprocess_service.resolve_steps([{"op": "l0_smooth"}])[0]
    assert step["border"] == "periodic"
    assert step["working_space"] == "encoded_srgb"
    assert step["alpha_policy"] == "preserve_alpha_mean_extend_hidden_rgb"
    assert step["status"] == "experimental"


def test_approximate_operator_does_not_claim_exact_paper_method():
    step = postprocess_service.resolve_steps([{"op": "rtv_smooth"}])[0]
    assert step["approximation"] == "box_window_irls"


def test_engine_records_numpy_and_color_management_versions():
    engine = postprocess_service._engine()
    assert engine["numpy"]
    assert engine["littlecms"]
