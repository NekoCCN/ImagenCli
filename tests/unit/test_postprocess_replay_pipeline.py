"""Recorded pipelines can replay only after strict, non-executing resolution."""

import pytest

from imagen_cli.application import postprocess_service


def test_resolved_manifest_becomes_explicit_pipeline():
    manifest = {"kind": "postprocess", "schema_version": 2,
                "resolved_pipeline": postprocess_service.resolve_steps([
                    {"op": "brightness", "params": {"factor": 0.8}},
                ])}
    assert postprocess_service.replay_pipeline(manifest) == [
        {"op": "brightness", "algorithm_version": 1, "params": {"factor": 0.8}},
    ]


@pytest.mark.parametrize("manifest", [
    {"kind": "postprocess", "pipeline": [{"op": "kuwahara"}]},
    {"kind": "postprocess", "schema_version": 900},
    {"kind": "provider_output"},
])
def test_unbound_or_unknown_manifest_is_rejected(manifest):
    with pytest.raises(ValueError, match="replay"):
        postprocess_service.replay_pipeline(manifest)


def test_unknown_parameter_cannot_be_replayed():
    steps = postprocess_service.resolve_steps([{"op": "brightness"}])
    steps[0]["effective_params"]["new_parameter"] = 1
    with pytest.raises(ValueError, match="replay"):
        postprocess_service.replay_pipeline({"kind": "postprocess", "schema_version": 2,
                                            "resolved_pipeline": steps})
