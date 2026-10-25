"""Imported provenance is inspected, never executed or silently upgraded."""

from imagen_cli.application import postprocess_service


def test_missing_legacy_implementation_is_ambiguous():
    manifest = {"kind": "postprocess", "pipeline": [{"op": "kuwahara"}]}
    assert postprocess_service.replay_status(manifest)["status"] == "legacy_ambiguous"
    assert "algorithm_version" not in manifest["pipeline"][0]


def test_unknown_schema_remains_opaque():
    assert postprocess_service.replay_status({
        "kind": "postprocess", "schema_version": 999, "custom": {"keep": True},
    })["status"] == "unsupported"


def test_recorded_current_pipeline_is_replayable():
    manifest = {"kind": "postprocess", "schema_version": 2,
                "resolved_pipeline": postprocess_service.resolve_steps([{"op": "guided_smooth"}])}
    assert postprocess_service.replay_status(manifest)["status"] == "replayable"


def test_unknown_version_is_not_replaced_with_current_version():
    step = postprocess_service.resolve_steps([{"op": "guided_smooth"}])[0]
    step["algorithm_version"] = 900
    result = postprocess_service.replay_status({"kind": "postprocess", "schema_version": 2,
                                              "resolved_pipeline": [step]})
    assert result["status"] == "unavailable"
    assert result["step"] == 0
    assert step["algorithm_version"] == 900


def test_changed_implementation_identity_is_not_replayable():
    step = postprocess_service.resolve_steps([{"op": "guided_smooth"}])[0]
    step["implementation"] = "unknown.backend"
    assert postprocess_service.replay_status({"kind": "postprocess", "schema_version": 2,
        "resolved_pipeline": [step]})["status"] == "unavailable"


def test_reference_path_alone_does_not_prove_reference_available():
    step = postprocess_service.resolve_steps([{
        "op": "chroma_transfer", "params": {"reference": "/not/portable.png"},
    }])[0]
    assert postprocess_service.replay_status({"kind": "postprocess", "schema_version": 2,
        "resolved_pipeline": [step]})["status"] == "external_inputs_required"


def test_non_postprocess_metadata_is_not_interpreted():
    assert postprocess_service.replay_status({"kind": "provider_output"}) is None
