"""New recipes execute through the actual offline CLI, not only direct functions."""

import pytest

from imagen_cli.application.container import Container
from imagen_cli.application.session_service import SessionService
from tests.integration.test_local_cli import invoke


@pytest.mark.parametrize("arguments", [
    ("--preset", "local_laplacian_sampled"),
    ("--preset", "weighted_guided_smooth"),
    ("--preset", "fast_guided_smooth"),
    ("--preset", "weighted_median"),
    ("--preset", "periodic_notch_auto"),
    ("--op", "wls_detail:fine=0.5"),
    ("--op", "periodic_notch:frequencies=0.125,0,width=0.02"),
])
def test_experimental_operations_work_through_offline_cli(tmp_path, png_bytes, arguments):
    workspace = tmp_path / "work"
    with Container(workspace=str(workspace)) as container:
        service = SessionService(container.repositories, container.artifact_store)
        session = service.create_session("postprocess")
        turn = service.create_turn(session.id, instruction="offline", operation="generate",
                                   resolved_profile_snapshot={}, model="local", parameters={})
        source = service.save_artifact(turn.id, png_bytes)
    code, result = invoke(workspace, "transform", source.id, *arguments)
    assert code == 0, result
    output = result["data"]["artifact"]
    assert output["id"] != source.id
    assert output["origin_manifest"]["schema_version"] == 2
    assert output["origin_manifest"]["resolved_pipeline"][0]["status"] == "experimental"
    assert output["origin_manifest"]["execution"]["isolation"] == "spawn_process"
    with Container(workspace=str(workspace)) as container:
        assert container.artifact_store.get(source.id).read_bytes() == png_bytes
        assert container.artifact_store.get(output["id"]).is_file()


def test_cli_deadline_zero_disables_the_limit(tmp_path, png_bytes):
    workspace = tmp_path / "work"
    with Container(workspace=str(workspace)) as container:
        service = SessionService(container.repositories, container.artifact_store)
        session = service.create_session("deadline")
        turn = service.create_turn(session.id, instruction="offline", operation="generate",
                                   resolved_profile_snapshot={}, model="local", parameters={})
        source = service.save_artifact(turn.id, png_bytes)
    code, result = invoke(workspace, "transform", source.id, "--op", "invert",
                          "--timeout", "0")
    assert code == 0
    with Container(workspace=str(workspace)) as container:
        assert len(container.repositories.list("artifacts")) == 2


def test_cli_replays_resolved_manifest_against_explicit_source(tmp_path, png_bytes):
    import json

    workspace = tmp_path / "work"
    with Container(workspace=str(workspace)) as container:
        service = SessionService(container.repositories, container.artifact_store)
        session = service.create_session("replay")
        turn = service.create_turn(session.id, instruction="offline", operation="generate",
                                   resolved_profile_snapshot={}, model="local", parameters={})
        source = service.save_artifact(turn.id, png_bytes)
    code, first = invoke(workspace, "transform", source.id, "--op", "invert")
    assert code == 0
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(first["data"]["artifact"]["origin_manifest"]))
    code, second = invoke(workspace, "transform", source.id, "--pipeline", str(manifest_file))
    assert code == 0, second
    assert first["data"]["artifact"]["sha256"] == second["data"]["artifact"]["sha256"]
    assert first["data"]["artifact"]["id"] != second["data"]["artifact"]["id"]


def test_cli_accepts_pipeline_array_file(tmp_path, png_bytes):
    import json

    workspace = tmp_path / "work"
    with Container(workspace=str(workspace)) as container:
        service = SessionService(container.repositories, container.artifact_store)
        session = service.create_session("pipeline")
        turn = service.create_turn(session.id, instruction="offline", operation="generate",
                                   resolved_profile_snapshot={}, model="local", parameters={})
        source = service.save_artifact(turn.id, png_bytes)
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps([{"op": "invert"}]))
    code, result = invoke(workspace, "transform", source.id, "--pipeline", str(pipeline))
    assert code == 0, result
