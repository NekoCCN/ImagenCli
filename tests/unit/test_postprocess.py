"""Postprocess: new artifacts, provenance, determinism, limits."""

from pathlib import Path

import pytest
from PIL import Image

from imagen_cli.application import postprocess_service
from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.database import connect
from imagen_cli.storage.repositories import Repositories


@pytest.fixture
def workspace(tmp_path: Path, png_bytes: bytes):
    root = tmp_path / "workspace"
    root.mkdir()
    repositories = Repositories(connect(root / "state.sqlite3"))
    store = ArtifactStore(root / "artifacts", repositories)
    repositories.insert(
        "sessions", {"id": "ses_a", "name": "a", "version": 0, "created_at": "2026-10-24T00:00:00Z"}
    )
    store.stage("art_" + "a" * 32, png_bytes)
    store.publish("art_" + "a" * 32)
    repositories.insert(
        "artifact_links",
        {"session_id": "ses_a", "artifact_id": "art_" + "a" * 32, "role": "output",
         "created_at": "2026-10-24T00:00:00Z"},
    )
    yield repositories, store
    repositories.connection.close()


def test_transform_creates_new_artifact_and_keeps_source(workspace):
    repositories, store = workspace
    source = repositories.get("artifacts", "art_" + "a" * 32)
    result = postprocess_service.apply(
        repositories, store, source["id"], [{"op": "resize", "params": {"scale": 2}}]
    )
    assert result["id"] != source["id"]
    assert result["width"] == source["width"] * 2
    assert result["origin_manifest"]["kind"] == "postprocess"
    assert result["origin_manifest"]["source_sha256"] == source["sha256"]
    assert repositories.get("artifacts", source["id"])["sha256"] == source["sha256"]
    links = repositories.list("artifact_links", session_id="ses_a")
    assert result["id"] in {row["artifact_id"] for row in links}


def test_same_pipeline_is_byte_identical(workspace):
    repositories, store = workspace
    pipeline = [{"op": "pixelate", "params": {"scale": 2}},
                {"op": "posterize", "params": {"levels": 2}}]
    first = postprocess_service.apply(repositories, store, "art_" + "a" * 32, pipeline)
    second = postprocess_service.apply(repositories, store, "art_" + "a" * 32, pipeline)
    assert first["sha256"] == second["sha256"]


def test_unknown_op_and_step_limit_are_refused(workspace):
    repositories, store = workspace
    with pytest.raises(ValueError, match="unknown operation"):
        postprocess_service.apply(repositories, store, "art_" + "a" * 32, [{"op": "nope"}])
    with pytest.raises(ValueError, match="step limit"):
        postprocess_service.apply(
            repositories, store, "art_" + "a" * 32,
            [{"op": "grayscale"} for _ in range(21)],
        )


def test_missing_owner_requires_explicit_session(workspace, tmp_path):
    repositories, store = workspace
    repositories.insert(
        "sessions", {"id": "ses_b", "name": "b", "version": 0, "created_at": "2026-10-24T00:00:00Z"}
    )
    repositories.insert(
        "artifact_links",
        {"session_id": "ses_b", "artifact_id": "art_" + "a" * 32, "role": "output",
         "created_at": "2026-10-24T00:00:00Z"},
    )
    with pytest.raises(ValueError, match="several sessions"):
        postprocess_service.apply(repositories, store, "art_" + "a" * 32, [{"op": "grayscale"}])
    result = postprocess_service.apply(
        repositories, store, "art_" + "a" * 32, [{"op": "grayscale"}], session_id="ses_b",
    )
    assert Image.open(store.get(result["id"])).mode == "RGB"


def test_encode_step_sets_the_media_type_and_is_recorded(workspace):
    repositories, store = workspace
    pipeline = [{"op": "resize", "params": {"scale": 2}},
                {"op": "encode", "params": {"format": "jpeg", "quality": 80}}]
    result = postprocess_service.apply(repositories, store, "art_" + "a" * 32, pipeline)
    assert result["media_type"] == "image/jpeg"
    assert result["origin_manifest"]["pipeline"] == pipeline


def test_encode_must_be_the_final_step(workspace):
    repositories, store = workspace
    with pytest.raises(ValueError, match="final step"):
        postprocess_service.validate(
            [{"op": "encode", "params": {"format": "png"}}, {"op": "grayscale"}]
        )
