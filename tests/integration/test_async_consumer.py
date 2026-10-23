"""Queued async jobs run once from their frozen turn snapshot."""

from typing import Any

import pytest

from imagen_cli.application.container import Container
from imagen_cli.application.session_service import SessionService
from imagen_cli.domain.primitive import new_id
from imagen_cli.execution.queued import run_queued
from imagen_cli.storage.repositories import Repositories
from tests.integration.test_approved_execution import OfflineAdapter


def _queued(
    container: Container, png_bytes: bytes, *, name: str = "async", with_input: bool = False,
) -> tuple[Any, str | None]:
    repositories: Repositories = container.repositories
    repositories.insert("profiles", {
        "id": "queued", "name": "queued", "provider": "openai", "family": "images",
        "base_url": "https://offline.example.test/v1", "model_default": "image-model",
        "credential_ref": "fixture-not-resolved", "revision": 1,
        "trust_policy": {"allowed_upload_targets": ["https://offline.example.test/v1"]},
    })
    sessions = SessionService(repositories, container.artifact_store)
    session = sessions.create_session(name, default_profile_id="queued",
                                      default_model="image-model")
    identity = None
    references = None
    if with_input:
        identity = new_id("art")
        container.artifact_store.stage(identity, png_bytes)
        container.artifact_store.publish(identity)
        references = {"input": [identity]}
    turn = sessions.create_turn(
        session.id, instruction="red kite", operation="generate",
        resolved_profile_snapshot={
            "profile_identity": "queued", "profile_revision": 1,
            "endpoint_origin": "https://offline.example.test/v1",
            "allowed_upload_targets": ["https://offline.example.test/v1"],
            "provider": "openai", "family": "images", "model_revision": "local",
        },
        model="image-model", parameters={"count": 1}, requested_count=1,
        references=references,
    )
    repositories.update("jobs", turn.job_id, {"worker_queued": True})
    return turn, identity


def test_queued_job_runs_once_and_saves_the_artifact(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        turn, _ = _queued(container, png_bytes)
        adapter = OfflineAdapter(png_bytes)
        result = run_queued(
            container.repositories, container.artifact_store,
            container.private_state_store, adapter, turn.job_id,
        )
        assert result["status"] == "succeeded"
        assert len(adapter.requests) == 1
        job = container.repositories.get("jobs", turn.job_id)
        assert job["state"] == "succeeded" and job["actual_count"] == 1
        assert container.repositories.get("turns", turn.id)["creation_state"] == "succeeded"
        artifacts = container.repositories.list("artifacts", producer_job_id=turn.job_id)
        assert len(artifacts) == 1
        assert container.artifact_store.get(artifacts[0]["id"]).read_bytes() == png_bytes
        with pytest.raises(ValueError):
            run_queued(
                container.repositories, container.artifact_store,
                container.private_state_store, adapter, turn.job_id,
            )
        assert len(adapter.requests) == 1


def test_queued_job_carries_input_bytes(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        turn, identity = _queued(container, png_bytes, with_input=True)
        adapter = OfflineAdapter(png_bytes)
        result = run_queued(
            container.repositories, container.artifact_store,
            container.private_state_store, adapter, turn.job_id,
        )
        assert result["status"] == "succeeded"
        assert adapter.requests[0].inputs == (png_bytes,)
        assert identity is not None


def test_queued_job_marks_unknown_and_never_redispatches(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        turn, _ = _queued(container, png_bytes)
        adapter = OfflineAdapter(png_bytes, error=RuntimeError("uncertain"))
        result = run_queued(
            container.repositories, container.artifact_store,
            container.private_state_store, adapter, turn.job_id,
        )
        assert result["status"] == "unknown"
        job = container.repositories.get("jobs", turn.job_id)
        assert job["accepted_maybe"] is True
        assert job["error"] == {"type": "outcome_unknown", "error_class": "RuntimeError"}
        assert len(adapter.requests) == 1
        with pytest.raises(ValueError):
            run_queued(
                container.repositories, container.artifact_store,
                container.private_state_store, adapter, turn.job_id,
            )
        assert len(adapter.requests) == 1


def test_queued_job_fails_without_sending_when_the_profile_drifted(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        turn, _ = _queued(container, png_bytes)
        container.repositories.update("profiles", "queued", {"revision": 2})
        adapter = OfflineAdapter(png_bytes)
        result = run_queued(
            container.repositories, container.artifact_store,
            container.private_state_store, adapter, turn.job_id,
        )
        assert result["status"] == "failed"
        assert adapter.requests == []
        job = container.repositories.get("jobs", turn.job_id)
        assert job["state"] == "failed" and job["accepted_maybe"] is False


def test_queued_job_fails_without_sending_when_input_bytes_changed(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        turn, identity = _queued(container, png_bytes, with_input=True)
        assert identity is not None
        container.artifact_store.get(identity).write_bytes(b"tampered")
        adapter = OfflineAdapter(png_bytes)
        result = run_queued(
            container.repositories, container.artifact_store,
            container.private_state_store, adapter, turn.job_id,
        )
        assert result["status"] == "failed"
        assert adapter.requests == []
