"""Pipeline version and parameter validation must precede asset publication."""

import pytest

from imagen_cli.application import postprocess_service


def test_explicit_algorithm_version_is_preserved():
    assert postprocess_service.validate([
        {"op": "brightness", "algorithm_version": 1, "params": {"factor": 1}}
    ])[0]["algorithm_version"] == 1


@pytest.mark.parametrize("version", [0, 2, True, "1"])
def test_unknown_algorithm_version_is_rejected(version):
    with pytest.raises(ValueError, match="version"):
        postprocess_service.validate([
            {"op": "brightness", "algorithm_version": version}
        ])


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_parameters_are_rejected(value):
    with pytest.raises(ValueError, match="finite"):
        postprocess_service.validate([{"op": "brightness", "params": {"factor": value}}])


def test_unknown_parameter_is_rejected_before_processing():
    with pytest.raises(ValueError, match="no_such"):
        postprocess_service.validate([{"op": "brightness", "params": {"no_such": 1}}])
