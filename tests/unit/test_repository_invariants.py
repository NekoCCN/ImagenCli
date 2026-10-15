import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from imagen_cli.storage.database import connect
from imagen_cli.storage.repositories import Repositories


@pytest.fixture
def repo(tmp_path):
    db = connect(tmp_path / "state.sqlite3")
    repository = Repositories(db)
    repository.insert("sessions", {"id": "ses_a", "name": "a"})
    repository.insert("sessions", {"id": "ses_b", "name": "b"})
    for suffix in "abc":
        repository.insert("turns", {"id": f"trn_{suffix}", "session_id": "ses_a"})
        repository.insert("jobs", {"id": f"job_{suffix}", "turn_id": f"trn_{suffix}"})
    yield repository
    db.close()


def approval(repo, **plan_changes):
    plan = {
        "id": "pln_a",
        "session_id": "ses_a",
        "plan_digest": "a" * 64,
        "parent_turn_id": "trn_a",
        "expected_session_version": 0,
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }
    plan.update(plan_changes)
    repo.insert("plans", plan)
    repo.insert(
        "approvals",
        {
            "id": "apr_a",
            "plan_id": "pln_a",
            "plan_digest": "a" * 64,
            "state": "approved",
        },
    )


def consume(repo, **changes):
    turn = {
        "id": "trn_exec",
        "session_id": "ses_a",
        "parent_turn_id": "trn_a",
        "expected_session_version": 0,
    }
    job = {"id": "job_exec", "turn_id": "trn_exec", "plan_id": "pln_a"}
    turn.update(changes.get("turn", {}))
    job.update(changes.get("job", {}))
    return repo.consume_approval("apr_a", "a" * 64, turn, job)


@pytest.mark.parametrize(
    "origin",
    [
        {"origin_session_id": "missing"},
        {"origin_session_id": "ses_a", "origin_turn_id": "missing"},
        {"origin_session_id": "ses_b", "origin_turn_id": "trn_a"},
        {"origin_turn_id": "trn_a"},
    ],
)
def test_origin_relations_reject_invalid_sources_on_insert_and_update(repo, origin):
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        repo.insert("sessions", {"id": "ses_fork", "name": "fork", **origin})
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        repo.update("sessions", "ses_b", origin)
    assert "origin_turn_id" not in repo.get("sessions", "ses_b")


def test_valid_origin_preserves_source_session(repo):
    source = repo.get("sessions", "ses_a")
    repo.insert(
        "sessions",
        {"id": "ses_fork", "name": "fork", "origin_session_id": "ses_a", "origin_turn_id": "trn_a"},
    )
    assert repo.get("sessions", "ses_fork")["origin_turn_id"] == "trn_a"
    assert repo.get("sessions", "ses_a") == source


@pytest.mark.parametrize("children", [["job_b"], ["job_b", "job_a"], ["job_b", "job_c"]])
def test_batch_child_updates_rejected_without_changing_payload_rows_or_jobs(repo, children):
    repo.insert("batches", {"id": "bat_a", "child_job_ids": ["job_a", "job_b"]})
    batch = repo.get("batches", "bat_a")
    jobs = repo.list("jobs")
    rows = list(repo.connection.execute("SELECT * FROM batch_children ORDER BY position"))
    with pytest.raises(ValueError, match="immutable"):
        repo.update("batches", "bat_a", {"child_job_ids": children})
    assert repo.get("batches", "bat_a") == batch
    assert repo.list("jobs") == jobs
    assert list(repo.connection.execute("SELECT * FROM batch_children ORDER BY position")) == rows
    repo.update("batches", "bat_a", {"child_job_ids": ["job_a", "job_b"]})
    assert repo.get("batches", "bat_a") == batch


def test_batch_creation_cannot_steal_an_existing_child_and_is_atomic(repo):
    repo.insert("batches", {"id": "bat_a", "child_job_ids": ["job_a"]})
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        repo.insert("batches", {"id": "bat_b", "child_job_ids": ["job_c", "job_a"]})
    assert repo.get("batches", "bat_b") is None
    assert repo.get("jobs", "job_a")["batch_id"] == "bat_a"
    assert repo.get("jobs", "job_c")["batch_id"] is None
    assert [tuple(row) for row in repo.connection.execute("SELECT * FROM batch_children")] == [
        ("bat_a", "job_a", 0)
    ]


def test_jobs_cannot_bypass_batch_membership(repo):
    repo.insert("batches", {"id": "bat_a", "child_job_ids": ["job_a"]})
    with pytest.raises(ValueError):
        repo.update("jobs", "job_a", {"batch_id": None})
    with pytest.raises(ValueError):
        repo.update("jobs", "job_b", {"batch_id": "bat_a"})
    assert repo.get("jobs", "job_a")["batch_id"] == "bat_a"
    assert repo.get("jobs", "job_b")["batch_id"] is None


def test_batch_child_update_rolls_back_all_links_on_conflict(repo):
    repo.insert("batches", {"id": "bat_a", "child_job_ids": ["job_a"]})
    repo.insert("batches", {"id": "bat_b", "child_job_ids": ["job_b"]})
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        repo.update("batches", "bat_a", {"child_job_ids": ["job_c", "job_b"]})
    assert repo.get("batches", "bat_a")["child_job_ids"] == ["job_a"]
    assert repo.get("jobs", "job_a")["batch_id"] == "bat_a"
    assert repo.get("jobs", "job_b")["batch_id"] == "bat_b"
    assert repo.get("jobs", "job_c")["batch_id"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"turn": {"session_id": "ses_b", "parent_turn_id": None}},
        {"turn": {"parent_turn_id": "trn_b"}},
        {"turn": {"expected_session_version": 1}},
        {"job": {"plan_id": "other"}},
        {"job": {"turn_id": "trn_b"}},
    ],
)
def test_approval_rejects_mismatched_plan_session_parent_version_and_job(repo, changes):
    approval(repo)
    with pytest.raises(ValueError):
        consume(repo, **changes)
    assert repo.get("turns", "trn_exec") is None
    assert repo.get("jobs", "job_exec") is None
    assert repo.get("approvals", "apr_a")["state"] == "approved"


@pytest.mark.parametrize("expired", ["plan", "approval"])
def test_expired_plan_or_approval_is_not_consumed(repo, expired):
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    approval(repo, **({"expires_at": past} if expired == "plan" else {}))
    repo.update(
        "approvals",
        "apr_a",
        {
            "expires_at": past
            if expired == "approval"
            else (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        },
    )
    with pytest.raises(ValueError, match="expired"):
        consume(repo)
    assert repo.get("turns", "trn_exec") is None
    assert repo.get("approvals", "apr_a")["state"] == "approved"


def test_approval_requires_plan_digest_match(repo):
    approval(repo, plan_digest="b" * 64)
    with pytest.raises(ValueError):
        consume(repo)
    assert repo.get("turns", "trn_exec") is None


@pytest.mark.parametrize("change", [{"version": 1}, {"deleted_at": "2026-10-01T00:00:00Z"}])
def test_approval_rejects_stale_or_deleted_session(repo, change):
    approval(repo)
    repo.update("sessions", "ses_a", change)
    with pytest.raises(ValueError):
        consume(repo)
    assert repo.get("turns", "trn_exec") is None


def test_approval_consumption_binds_job_and_repeats_without_recreating(repo):
    approval(repo)
    assert consume(repo) == "job_exec"
    assert repo.get("jobs", "job_exec")["plan_id"] == "pln_a"
    repo.update("jobs", "job_exec", {"state": "unknown"})
    repo.update("approvals", "apr_a", {"expires_at": "2000-01-01T00:00:00Z"})
    assert repo.consume_approval("apr_a", "a" * 64, {}, {}) == "job_exec"
    assert len(repo.list("jobs")) == 4


def test_approval_consumption_rolls_back_if_job_insert_fails(repo):
    approval(repo)
    with pytest.raises(sqlite3.IntegrityError):
        consume(repo, job={"id": "job_a"})
    assert repo.get("turns", "trn_exec") is None
    assert repo.get("approvals", "apr_a")["state"] == "approved"


def test_job_insert_cannot_claim_batch_without_child_row(repo):
    repo.insert("batches", {"id": "bat_a", "child_job_ids": ["job_a"]})
    repo.insert("turns", {"id": "trn_new", "session_id": "ses_a"})
    with pytest.raises(ValueError):
        repo.insert("jobs", {"id": "job_new", "turn_id": "trn_new", "batch_id": "bat_a"})
    assert repo.get("jobs", "job_new") is None


def test_consumed_approval_cannot_be_rebound_or_reopened(repo):
    approval(repo)
    consume(repo)
    for changes in (
        {"state": "approved"},
        {"execution_job_id": "job_b"},
        {"plan_id": "other"},
        {"plan_digest": "b" * 64},
    ):
        with pytest.raises(ValueError):
            repo.update("approvals", "apr_a", changes)
    with pytest.raises(ValueError):
        repo.update("jobs", "job_exec", {"plan_id": None})
    assert repo.get("approvals", "apr_a")["execution_job_id"] == "job_exec"


@pytest.mark.parametrize("expires", [None, "not-a-date", "2026-10-23T12:00:00"])
def test_plan_cannot_disable_ttl(repo, expires):
    with pytest.raises(ValueError):
        repo.insert("plans", {"id": "pln_a", "session_id": "ses_a", "expires_at": expires})


def test_plan_defaults_to_24_hour_ttl(repo):
    before = datetime.now(UTC)
    repo.insert("plans", {"id": "pln_a", "session_id": "ses_a"})
    after = datetime.now(UTC)
    expires = datetime.fromisoformat(repo.get("plans", "pln_a")["expires_at"])
    assert before + timedelta(hours=24) <= expires <= after + timedelta(hours=24)
