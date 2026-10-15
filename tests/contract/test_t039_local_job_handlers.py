"""Local job observation/recovery never has a provider/create dependency."""

import io
from pathlib import Path

import pytest
from PIL import Image

from imagen_cli.application.artifact_service import ArtifactService
from imagen_cli.application.session_service import SessionService
from imagen_cli.cli.commands import job
from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.database import connect
from imagen_cli.storage.repositories import Repositories


@pytest.fixture
def work(tmp_path: Path):
    connection = connect(tmp_path / "state.sqlite3")
    repo = Repositories(connection)
    store = ArtifactStore(tmp_path / "artifacts", repo)
    sessions = SessionService(repo, store)
    created = sessions.create_session("local")
    turn = sessions.create_turn(
        created.id,
        instruction="p",
        operation="generate",
        resolved_profile_snapshot={},
        model="local",
        parameters={},
    )
    yield ArtifactService(repo, store), turn.job_id
    connection.close()


def test_get_unknown_is_successful_query_but_wait_preserves_unknown(work):
    service, identity = work
    service.repositories.update("jobs", identity, {"state": "unknown", "accepted_maybe": True})
    assert job.get_(service, identity)[0] == "succeeded"
    status, data = job.wait_(service, identity)
    assert status == "unknown"
    assert data["job"]["accepted_maybe"] is True
    assert data["next_actions"][0]["creates_paid_request"] is False


def test_pending_wait_is_bounded_and_does_not_change_job(work, monkeypatch):
    service, identity = work
    before = service.repositories.get("jobs", identity)
    clock = iter([0.0, 0.0, 1.0])
    monkeypatch.setattr("imagen_cli.cli.commands.job.time.monotonic", lambda: next(clock))
    monkeypatch.setattr("imagen_cli.cli.commands.job.time.sleep", lambda _: None)
    assert job.wait_(service, identity, timeout=1)[0] == "wait_timeout"
    assert service.repositories.get("jobs", identity) == before
    assert len(service.repositories.list("jobs")) == 1


def test_fetch_requires_real_saved_files_and_never_recreates(work):
    service, identity = work
    assert job.fetch_(service, identity)[0] == "unsupported_capability"
    image = io.BytesIO()
    Image.new("RGB", (2, 2)).save(image, format="PNG")
    row = service.save(identity, image.getvalue())
    service.repositories.update(
        "jobs",
        identity,
        {
            "state": "succeeded",
            "remote_state": "succeeded",
            "local_save_state": "saved",
            "actual_count": 1,
        },
    )
    status, data = job.fetch_(service, identity)
    assert status == "succeeded"
    path = Path(data["artifacts"][0]["path"])
    assert path.is_absolute() and path.is_file()
    path.unlink()
    assert job.fetch_(service, identity)[0] == "save_failed"
    assert len(service.repositories.list("jobs")) == 1
    assert service.repositories.get("artifacts", row["id"])["save_state"] == "published"


def test_reconcile_existing_staging_only_and_preserves_unknown(work):
    service, identity = work
    image = io.BytesIO()
    Image.new("RGB", (2, 2)).save(image, format="PNG")
    turn_id = service.repositories.get("jobs", identity)["turn_id"]
    artifact_id = "art_" + "1" * 32
    service.artifact_store.stage(
        artifact_id,
        image.getvalue(),
        producer_job_id=identity,
        producer_turn_id=turn_id,
        origin_manifest={"kind": "provider_output", "output_index": 0},
    )
    service.repositories.update("jobs", identity, {"state": "unknown"})
    status, data = job.reconcile_(service, identity)
    assert status == "succeeded"
    assert Path(data["artifacts"][0]["path"]).exists()
    assert data["job"]["state"] == "unknown"
    assert len(service.repositories.list("jobs")) == 1


def test_wait_does_not_trust_success_metadata_without_saved_files(work):
    service, identity = work
    service.repositories.update(
        "jobs", identity,
        {"state": "succeeded", "remote_state": "succeeded",
         "local_save_state": "saved", "actual_count": 1},
    )
    assert job.wait_(service, identity)[0] == "save_failed"


def test_fetch_partial_local_copy_does_not_claim_complete_remote_fetch(work):
    service, identity = work
    image = io.BytesIO()
    Image.new("RGB", (2, 2)).save(image, format="PNG")
    service.save(identity, image.getvalue())
    service.repositories.update("jobs", identity, {"actual_count": 2, "remote_state": "succeeded"})
    assert job.fetch_(service, identity)[0] == "partial_success"


def test_missing_jobs_and_unsupported_reconcile(work):
    service, identity = work
    for handler in (job.get_, job.wait_, job.fetch_, job.reconcile_):
        assert handler(service, "job_" + "2" * 32)[0] == "not_found"
    assert job.reconcile_(service, identity)[0] == "unsupported_capability"


@pytest.mark.parametrize("timeout", [True, 0, 86401])
def test_invalid_wait_deadline(work, timeout):
    with pytest.raises(ValueError):
        job.wait_(work[0], work[1], timeout=timeout)
