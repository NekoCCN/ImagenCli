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
    with pytest.raises(ValueError, match="nope"):
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
    with Image.open(store.get(result["id"])) as output:
        assert output.info.get("icc_profile")
    assert result["origin_manifest"]["pipeline"] == pipeline


def test_encode_must_be_the_final_step(workspace):
    repositories, store = workspace
    with pytest.raises(ValueError, match="final step"):
        postprocess_service.validate(
            [{"op": "encode", "params": {"format": "png"}}, {"op": "grayscale"}]
        )


def test_advanced_pipeline_with_float_params(workspace):
    from imagen_cli.cli.commands import postprocess

    parsed = [
        postprocess.parse_op("film_grain:amount=0.06,monochrome=true,seed=42"),
        postprocess.parse_op("filmic_tone:lift_black=6,compress_white=245"),
        postprocess.parse_op("vignette:radius=0.75,feather=0.5,darkness=0.3"),
    ]
    assert parsed[0]["params"]["amount"] == 0.06
    assert parsed[0]["params"]["monochrome"] is True
    assert parsed[0]["params"]["seed"] == 42
    assert parsed[2]["params"]["radius"] == 0.75

    repositories, store = workspace
    result = postprocess_service.apply(repositories, store, "art_" + "a" * 32, parsed)
    assert result["id"] != "art_" + "a" * 32
    assert result["origin_manifest"]["kind"] == "postprocess"


def test_paper_based_pipeline_execution(workspace):
    from imagen_cli.cli.commands import postprocess

    parsed = [
        postprocess.parse_op("aces_tonemap:exposure=1.1,contrast=1.05"),
        postprocess.parse_op("guided_filter:radius=2,eps=0.04,boost=1.6"),
        postprocess.parse_op("kuwahara:size=3"),
        postprocess.parse_op("anamorphic_flare:threshold=180,length=24,tint=#4a90e2"),
        postprocess.parse_op("color_transfer:style=anime_sky,strength=0.8"),
    ]
    assert parsed[0]["op"] == "aces_tonemap"
    assert parsed[0]["params"]["exposure"] == 1.1
    assert parsed[1]["op"] == "guided_filter"
    assert parsed[1]["params"]["boost"] == 1.6
    assert parsed[3]["op"] == "anamorphic_flare"
    assert parsed[3]["params"]["tint"] == "#4a90e2"

    repositories, store = workspace
    result = postprocess_service.apply(repositories, store, "art_" + "a" * 32, parsed)
    assert result["id"] != "art_" + "a" * 32
    assert result["origin_manifest"]["kind"] == "postprocess"
    assert len(result["origin_manifest"]["pipeline"]) == 5
def test_reference_image_identity_is_recorded(workspace, tmp_path):
    import hashlib

    repositories, store = workspace
    reference = tmp_path / "reference.png"
    Image.new("RGB", (5, 4), (100, 90, 80)).save(reference)
    result = postprocess_service.apply(repositories, store, "art_" + "a" * 32, [
        {"op": "chroma_transfer", "params": {"reference": str(reference)}},
    ])
    record = result["origin_manifest"]["references"][0]
    assert record["step"] == 0
    assert record["sha256"] == hashlib.sha256(reference.read_bytes()).hexdigest()
    assert record["color"]["input_profile"] == "assumed_srgb"


def test_timeout_never_publishes_partial_artifact(workspace):
    repositories, store = workspace
    before = repositories.list("artifacts")
    with pytest.raises(ValueError, match="timeout"):
        postprocess_service.apply(repositories, store, "art_" + "a" * 32,
                                  [{"op": "invert"}], timeout=0.001)
    assert repositories.list("artifacts") == before


def test_cancelled_pipeline_never_publishes_partial_artifact(workspace):
    import threading

    repositories, store = workspace
    cancelled = threading.Event()
    cancelled.set()
    before = repositories.list("artifacts")
    with pytest.raises(ValueError, match="cancelled"):
        postprocess_service.apply(repositories, store, "art_" + "a" * 32,
                                  [{"op": "invert"}], cancelled=cancelled)
    assert repositories.list("artifacts") == before


def test_cancellation_at_worker_return_does_not_publish(workspace, monkeypatch):
    import threading

    repositories, store = workspace
    cancelled = threading.Event()
    before = repositories.list("artifacts")

    def completed_but_cancelled(*args, **kwargs):
        cancelled.set()
        return b"not published", {}

    monkeypatch.setattr(
        "imagen_cli.application.postprocess_worker.execute", completed_but_cancelled,
    )
    with pytest.raises(ValueError, match="cancelled"):
        postprocess_service.apply(repositories, store, "art_" + "a" * 32,
                                  [{"op": "invert"}], cancelled=cancelled)
    assert repositories.list("artifacts") == before

