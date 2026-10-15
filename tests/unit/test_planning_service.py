from datetime import timedelta

import pytest

from imagen_cli.application.planning_service import PlanningService
from imagen_cli.storage.database import connect
from imagen_cli.storage.repositories import Repositories


def _stamp(second: int) -> str:
    return f"2026-10-23T12:00:{second:02d}Z"


def _target(**changes):
    value = {
        "profile_identity": "profile-openai",
        "profile_revision": 3,
        "endpoint_origin": "https://api.example.test/v1",
        "allowed_upload_targets": ["https://api.example.test/v1"],
    }
    value.update(changes)
    return value


def _service(tmp_path, *, now="2026-10-23T12:00:00Z"):
    connection = connect(tmp_path / "state.sqlite3")
    repositories = Repositories(connection)
    repositories.insert("sessions", {"id": "ses_1234567890124abc89abcdef123456ab", "name": "work"})
    repositories.insert(
        "turns",
        {
            "id": "trn_1234567890124abc89abcdef123456ab",
            "session_id": "ses_1234567890124abc89abcdef123456ab",
            "creation_state": "succeeded",
        },
    )
    repositories.update(
        "sessions",
        "ses_1234567890124abc89abcdef123456ab",
        {"current_turn_id": "trn_1234567890124abc89abcdef123456ab"},
    )
    return connection, PlanningService(repositories, clock=lambda: now)


def _plan(service, **changes):
    values = {
        "session_id": "ses_1234567890124abc89abcdef123456ab",
        "target_snapshot": _target(),
        "operation": "generate",
        "model": "image-model",
        "normalized_parameters": {"count": 1, "size": "1024x1024"},
        "prompt_snapshot": "a red kite",
        "input_hashes": ["a" * 64],
        "portable_context": {"kind": "none"},
        "capability_digest": "b" * 64,
        "context_loss": ["native_context_not_transferred"],
        "approval_required": True,
    }
    values.update(changes)
    return service.create_plan(**values)


def _execution_inputs(plan):
    turn = {
        "id": "trn_2234567890124abc89abcdef123456ab",
        "session_id": plan.session_id,
        "parent_turn_id": plan.parent_turn_id,
        "expected_session_version": plan.expected_session_version,
        "instruction": plan.prompt_snapshot,
        "operation": plan.operation,
        "model": plan.model,
        "parameters": plan.normalized_parameters,
        "input_manifest": [{"sha256": value} for value in plan.input_hashes],
        "resolved_profile_snapshot": plan.target_snapshot,
    }
    job = {"id": "job_1234567890124abc89abcdef123456ab", "turn_id": turn["id"], "plan_id": plan.id}
    return turn, job


def test_create_plan_freezes_digest_and_requires_parent_and_target_identity(tmp_path):
    connection, service = _service(tmp_path)
    try:
        plan, approval = _plan(service)
        assert plan.parent_turn_id == "trn_1234567890124abc89abcdef123456ab"
        assert approval is not None and approval.plan_digest == plan.plan_digest
        persisted = service.repositories.get("plans", plan.id)
        assert persisted["plan_digest"] == plan.plan_digest
        with pytest.raises(ValueError, match="immutable"):
            service.repositories.update("plans", plan.id, {"prompt_snapshot": "changed"})
        with pytest.raises(ValueError, match="target_snapshot"):
            _plan(service, target_snapshot={"profile_identity": "profile-openai"})
    finally:
        connection.close()


def test_approval_does_not_execute_and_expiry_or_digest_change_is_rejected(tmp_path):
    connection, service = _service(tmp_path, now=_stamp(0))
    try:
        plan, _ = _plan(service, ttl=timedelta(seconds=2))
        approved = service.approve(plan.id, plan.plan_digest)
        assert approved.state == "approved"
        assert service.repositories.get("turns", "trn_2234567890124abc89abcdef123456ab") is None
        with pytest.raises(ValueError, match="digest"):
            service.approve(plan.id, "c" * 64)
        expired = PlanningService(service.repositories, clock=lambda: _stamp(3))
        with pytest.raises(ValueError, match="expired"):
            expired.approve(plan.id, plan.plan_digest)
    finally:
        connection.close()


def test_execute_rechecks_current_snapshot_and_duplicate_returns_existing_job(tmp_path):
    connection, service = _service(tmp_path)
    try:
        plan, _ = _plan(service)
        service.approve(plan.id, plan.plan_digest)
        turn, job = _execution_inputs(plan)
        with pytest.raises(ValueError, match="snapshot"):
            service.execute_approved(
                plan.id, plan.plan_digest, turn, job, {**plan.model_dump(), "model": "other"}
            )
        assert service.repositories.get("approvals", plan.id) is None
        service.approve(plan.id, plan.plan_digest)
        assert (
            service.execute_approved(plan.id, plan.plan_digest, turn, job, plan.model_dump())
            == job["id"]
        )
        assert (
            service.execute_approved(plan.id, plan.plan_digest, turn, job, plan.model_dump())
            == job["id"]
        )
        assert len(service.repositories.list("jobs")) == 1
    finally:
        connection.close()


def test_local_plan_is_not_implicitly_authorized_and_execution_rolls_back(tmp_path):
    connection, service = _service(tmp_path)
    try:
        plan, approval = _plan(service, approval_required=False)
        assert approval is None
        turn, job = _execution_inputs(plan)
        with pytest.raises(ValueError, match="authorized"):
            service.execute_approved(plan.id, plan.plan_digest, turn, job, plan.model_dump())
        plan, _ = _plan(service)
        service.approve(plan.id, plan.plan_digest)
        turn, job = _execution_inputs(plan)
        with pytest.raises(ValueError, match="input_hashes"):
            service.execute_approved(
                plan.id,
                plan.plan_digest,
                {**turn, "input_manifest": [{"sha256": "d" * 64}]},
                job,
                plan.model_dump(),
            )
    finally:
        connection.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("instruction", "changed"),
        ("model", "changed"),
        ("operation", "edit"),
        ("parameters", {"count": 3}),
        ("resolved_profile_snapshot", {}),
        ("expected_session_version", 2),
        ("parent_turn_id", None),
    ],
)
def test_turn_substitution_is_refused(tmp_path, field, value):
    connection, service = _service(tmp_path)
    try:
        plan, approval = _plan(service)
        service.approve(plan.id, plan.plan_digest)
        turn, job = _execution_inputs(plan)
        with pytest.raises(ValueError):
            service.execute_approved(
                plan.id, plan.plan_digest, {**turn, field: value}, job, plan.model_dump()
            )
        assert service.repositories.get("approvals", approval.id)["state"] == "approved"
        assert service.repositories.list("jobs") == []
    finally:
        connection.close()


def test_persisted_content_tampering_invalidates_digest(tmp_path):
    connection, service = _service(tmp_path)
    try:
        plan, _ = _plan(service)
        connection.execute(
            "UPDATE plans SET payload=json_set(payload,'$.prompt_snapshot','changed') WHERE id=?",
            (plan.id,),
        )
        with pytest.raises(ValueError, match="digest"):
            service.approve(plan.id, plan.plan_digest)
    finally:
        connection.close()


def test_version_change_rejects_and_job_insert_failure_rolls_back(tmp_path):
    import sqlite3

    connection, service = _service(tmp_path)
    try:
        plan, approval = _plan(service)
        service.approve(plan.id, plan.plan_digest)
        turn, job = _execution_inputs(plan)
        service.repositories.update("sessions", plan.session_id, {"version": 1})
        with pytest.raises(ValueError, match="version"):
            service.execute_approved(plan.id, plan.plan_digest, turn, job, plan.model_dump())
        service.repositories.update("sessions", plan.session_id, {"version": 0})
        connection.execute(
            "CREATE TRIGGER fail_job BEFORE INSERT ON jobs "
            "BEGIN SELECT RAISE(ABORT,'injected'); END"
        )
        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            service.execute_approved(plan.id, plan.plan_digest, turn, job, plan.model_dump())
        assert service.repositories.get("turns", turn["id"]) is None
        assert service.repositories.get("approvals", approval.id)["state"] == "approved"
    finally:
        connection.close()


def test_consumed_unknown_job_returns_existing_id_even_after_expiry(tmp_path):
    connection, service = _service(tmp_path)
    try:
        plan, _ = _plan(service)
        service.approve(plan.id, plan.plan_digest)
        turn, job = _execution_inputs(plan)
        service.execute_approved(plan.id, plan.plan_digest, turn, job, plan.model_dump())
        service.repositories.update("jobs", job["id"], {"state": "unknown"})
        later = PlanningService(service.repositories, clock=lambda: "2026-10-25T12:00:00Z")
        assert later.execute_approved(plan.id, plan.plan_digest, {}, {}, {}) == job["id"]
        assert len(service.repositories.list("jobs")) == 1
    finally:
        connection.close()


@pytest.mark.parametrize(
    "target",
    [
        _target(profile_identity=""),
        _target(profile_revision=True),
        _target(profile_revision=0),
        _target(endpoint_origin="file:///tmp/upload"),
        _target(allowed_upload_targets="https://api.example.test"),
        _target(allowed_upload_targets=["https://user:password@example.test"]),
    ],
)
def test_invalid_target_boundaries_are_rejected(tmp_path, target):
    connection, service = _service(tmp_path)
    try:
        with pytest.raises(ValueError):
            _plan(service, target_snapshot=target)
        assert service.repositories.list("plans") == []
    finally:
        connection.close()


def test_job_initial_state_and_count_cannot_bypass_approval(tmp_path):
    connection, service = _service(tmp_path)
    try:
        plan, _ = _plan(service)
        service.approve(plan.id, plan.plan_digest)
        turn, job = _execution_inputs(plan)
        for changes in ({"requested_count": 4}, {"dispatch_state": "intent_recorded"}):
            with pytest.raises(ValueError):
                service.execute_approved(
                    plan.id, plan.plan_digest, turn, {**job, **changes}, plan.model_dump()
                )
        assert service.repositories.list("jobs") == []
    finally:
        connection.close()
