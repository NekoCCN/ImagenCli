"""Offline application publication and outcome contracts, using real SQLite/files."""

import hashlib

import pytest

from imagen_cli.application.container import Container


@pytest.fixture
def work(tmp_path):
    with Container(workspace=str(tmp_path)) as container:
        repo = container.repositories
        repo.insert("sessions", {"id": "ses_one", "name": "one"})
        repo.insert(
            "turns",
            {
                "id": "trn_one",
                "session_id": "ses_one",
                "expected_session_version": 0,
            },
        )
        repo.insert(
            "jobs",
            {
                "id": "job_one",
                "turn_id": "trn_one",
                "requested_count": 2,
                "actual_count": 0,
                "accepted_maybe": False,
            },
        )
        repo.update("turns", "trn_one", {"job_id": "job_one"})
        yield container


def artifact_api(work):
    from imagen_cli.application.artifact_service import ArtifactService

    return ArtifactService(work.repositories, work.artifact_store)


def outcome_api(work):
    from imagen_cli.application.outcome_service import OutcomeService

    return OutcomeService(work.repositories)


def test_save_measures_bytes_and_links_ordered_outputs(work, png_bytes):
    api = artifact_api(work)
    first = api.save("job_one", png_bytes, artifact_id="art_first", index=0)
    second = api.save("job_one", png_bytes, artifact_id="art_second", index=1)
    assert first["save_state"] == second["save_state"] == "published"
    assert (first["media_type"], first["width"], first["height"], first["byte_size"]) == (
        "image/png",
        2,
        3,
        len(png_bytes),
    )
    assert first["sha256"] == hashlib.sha256(png_bytes).hexdigest()
    assert work.artifact_store.get(first["id"]).read_bytes() == png_bytes
    refs = work.repositories.list("artifact_references", turn_id="trn_one")
    assert [(r["artifact_id"], r["index"]) for r in refs] == [("art_first", 0), ("art_second", 1)]
    assert work.repositories.get("sessions", "ses_one")["current_turn_id"] is None


def test_fetch_uses_existing_descriptor_and_ignores_declared_media(work, png_bytes):
    from imagen_cli.providers.base import ArtifactDescriptor

    descriptor = ArtifactDescriptor("art_result", 0, png_bytes, "image/jpeg")
    row = artifact_api(work).fetch("job_one", descriptor, lambda item: item.content)
    assert row["id"] == "art_result"
    assert row["media_type"] == "image/png"


def test_invalid_image_and_missing_job_do_not_publish(work, png_bytes):
    api = artifact_api(work)
    with pytest.raises(ValueError):
        api.save("job_one", b"not an image")
    with pytest.raises(KeyError):
        api.save("job_absent", png_bytes)
    assert work.repositories.list("artifacts") == []


def test_publish_failure_retains_staging_and_reconcile_restores_reference(
    work,
    png_bytes,
    monkeypatch,
):
    api = artifact_api(work)
    original = work.artifact_store.publish
    monkeypatch.setattr(
        work.artifact_store,
        "publish",
        lambda identity: (_ for _ in ()).throw(
            OSError("disk full secret URL"),
        ),
    )
    with pytest.raises(OSError):
        api.save("job_one", png_bytes, artifact_id="art_staged", index=1)
    assert work.repositories.get("artifacts", "art_staged")["save_state"] == "staging"
    monkeypatch.setattr(work.artifact_store, "publish", original)
    row = api.reconcile("art_staged")
    assert row["save_state"] == "published"
    assert work.artifact_store.get("art_staged").read_bytes() == png_bytes
    assert len(work.repositories.list("artifact_references")) == 1
    assert api.reconcile("art_staged")["id"] == row["id"]


def test_output_slot_cannot_be_overwritten(work, png_bytes):
    api = artifact_api(work)
    api.save("job_one", png_bytes, artifact_id="art_first", index=0)
    with pytest.raises((ValueError, FileExistsError)):
        api.save("job_one", png_bytes, artifact_id="art_second", index=0)
    assert work.repositories.get("artifacts", "art_second") is None
    assert work.artifact_store.get("art_first").read_bytes() == png_bytes


@pytest.mark.parametrize(
    "requested,actual,saved,status,code,job_state,local,turn_state,error",
    [
        (2, 2, 2, "succeeded", 0, "succeeded", "saved", "succeeded", None),
        (2, 1, 1, "partial_success", 9, "failed", "partial", "partial_success", "partial_failure"),
        (2, 2, 1, "partial_success", 9, "failed", "partial", "partial_success", "partial_failure"),
        (2, 2, 0, "save_failed", 7, "failed", "failed", "failed", "save_failed"),
        (2, 0, 0, "failed", 7, "failed", "not_started", "failed", "provider_error"),
        (1, 2, 2, "succeeded", 0, "succeeded", "saved", "succeeded", None),
    ],
)
def test_outcome_keeps_remote_success_and_only_publishes_complete_outputs(
    work,
    png_bytes,
    requested,
    actual,
    saved,
    status,
    code,
    job_state,
    local,
    turn_state,
    error,
):
    work.repositories.update("jobs", "job_one", {"requested_count": requested})
    for index in range(saved):
        artifact_api(work).save("job_one", png_bytes, index=index)
    result = outcome_api(work).complete("job_one", actual_count=actual)
    assert (result["status"], result["exit_code"], result["error_type"]) == (status, code, error)
    assert len(result["artifacts"]) == saved
    job = work.repositories.get("jobs", "job_one")
    assert (job["state"], job["remote_state"], job["local_save_state"]) == (
        job_state,
        "succeeded",
        local,
    )
    assert job["actual_count"] == actual
    turn = work.repositories.get("turns", "trn_one")
    assert turn["creation_state"] == turn_state
    session = work.repositories.get("sessions", "ses_one")
    assert session["current_turn_id"] == ("trn_one" if status == "succeeded" else None)
    if status in {"save_failed", "partial_success"}:
        assert result["next_actions"] == ["fetch", "reconcile"]


def test_successful_cas_conflict_preserves_job_and_files(work, png_bytes):
    repo = work.repositories
    repo.insert(
        "turns", {"id": "trn_winner", "session_id": "ses_one", "creation_state": "succeeded"}
    )
    assert repo.publish_turn("ses_one", "trn_winner", 0)
    for index in range(2):
        artifact_api(work).save("job_one", png_bytes, index=index)
    result = outcome_api(work).complete("job_one", actual_count=2)
    assert (result["status"], result["exit_code"]) == ("conflict", 5)
    assert result["next_actions"] == ["fork"]
    assert repo.get("jobs", "job_one")["state"] == "succeeded"
    assert repo.get("turns", "trn_one")["publication_state"] == "conflict"
    assert repo.get("sessions", "ses_one")["current_turn_id"] == "trn_winner"
    assert all(work.artifact_store.get(a["id"]).is_file() for a in result["artifacts"])


def test_complete_is_idempotent_and_does_not_republish(work, png_bytes):
    for index in range(2):
        artifact_api(work).save("job_one", png_bytes, index=index)
    api = outcome_api(work)
    assert api.complete("job_one", actual_count=2)["status"] == "succeeded"
    assert api.complete("job_one", actual_count=2)["status"] == "succeeded"
    assert work.repositories.get("sessions", "ses_one")["version"] == 1


@pytest.mark.parametrize(
    "accepted,status,remote", [(True, "unknown", "unknown"), (False, "failed", "failed")]
)
def test_provider_failure_does_not_replace_previous_success(work, accepted, status, remote):
    repo = work.repositories
    repo.insert("turns", {"id": "trn_old", "session_id": "ses_one", "creation_state": "succeeded"})
    repo.publish_turn("ses_one", "trn_old", 0)
    result = outcome_api(work).fail(
        "job_one", error_type="transport_error", accepted_maybe=accepted
    )
    assert result["status"] == status
    assert result["exit_code"] == (6 if accepted else 7)
    assert repo.get("jobs", "job_one")["remote_state"] == remote
    assert repo.get("jobs", "job_one")["accepted_maybe"] is accepted
    assert repo.get("turns", "trn_one")["creation_state"] == status
    assert repo.get("sessions", "ses_one")["current_turn_id"] == "trn_old"
    assert "create" not in result["next_actions"]


def test_failed_save_cannot_be_reclassified_as_remote_failure(work):
    api = outcome_api(work)
    api.complete("job_one", actual_count=2)
    result = api.fail("job_one", error_type="transport_error", accepted_maybe=True)
    assert result["status"] == "save_failed"
    assert work.repositories.get("jobs", "job_one")["remote_state"] == "succeeded"
    assert result["next_actions"] == ["fetch", "reconcile"]


def test_staging_outputs_are_not_returned_as_complete_files(work, png_bytes, monkeypatch):
    api = artifact_api(work)
    api.save("job_one", png_bytes, index=0)
    monkeypatch.setattr(
        work.artifact_store,
        "publish",
        lambda identity: (_ for _ in ()).throw(
            OSError("disk full"),
        ),
    )
    with pytest.raises(OSError):
        api.save("job_one", png_bytes, index=1)
    result = outcome_api(work).complete("job_one", actual_count=2)
    assert result["status"] == "partial_success"
    assert len(result["artifacts"]) == 1
    assert result["artifacts"][0]["save_state"] == "published"
    failed = outcome_api(work).fail("job_one", error_type="transport_error")
    assert failed["status"] == "partial_success"
    assert failed["exit_code"] == 9


def test_outcome_facts_roll_back_if_turn_update_fails(work, monkeypatch):
    original = work.repositories.update

    def update(table, identity, changes):
        if table == "turns":
            raise OSError("database failure")
        return original(table, identity, changes)

    monkeypatch.setattr(work.repositories, "update", update)
    with pytest.raises(OSError):
        outcome_api(work).complete("job_one", actual_count=2)
    assert work.repositories.get("jobs", "job_one")["state"] == "queued"


def test_inconsistent_actual_count_cannot_publish(work, png_bytes):
    for index in range(2):
        artifact_api(work).save("job_one", png_bytes, index=index)
    with pytest.raises(ValueError):
        outcome_api(work).complete("job_one", actual_count=1)
    assert work.repositories.get("jobs", "job_one")["state"] == "queued"


@pytest.mark.parametrize("actual", [-1, True, 1.5])
def test_invalid_actual_count_changes_no_facts(work, actual):
    with pytest.raises(ValueError):
        outcome_api(work).complete("job_one", actual_count=actual)
    assert work.repositories.get("jobs", "job_one")["state"] == "queued"
