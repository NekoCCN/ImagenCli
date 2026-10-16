"""Offline domain boundary tests, derived from the design and JSON schemas."""

import importlib
import json
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

ROOT = Path(__file__).resolve().parents[2]
STAMP = "2026-10-23T00:00:00Z"
HASH = "a" * 64


def identifier(prefix):
    return prefix + "_11111111111141118111111111111111"


def domain(module):
    # Missing implementation is an assertion failure rather than a collection error.
    try:
        return importlib.import_module("imagen_cli.domain." + module)
    except ModuleNotFoundError:
        pytest.fail("domain implementation missing: " + module)


def test_id_generation_and_prefix_validation():
    p = domain("primitive")
    for prefix in ("ses", "trn", "art", "job", "bat", "apr", "pln", "ctx", "evt", "wrk"):
        value = p.new_id(prefix)
        assert value.startswith(prefix + "_")
        assert UUID(value[4:]).version == 4
    adapter = TypeAdapter(p.SessionId)
    assert adapter.validate_python(identifier("ses")) == identifier("ses")
    for value in (identifier("job"), "ses_" + "0" * 32, identifier("ses").upper(), 1):
        with pytest.raises(ValidationError):
            adapter.validate_python(value)
    with pytest.raises(ValueError):
        p.new_id("wrong")


@pytest.mark.parametrize("value", [True, -1, "1", 1.0])
def test_nonnegative_integers_are_strict(value):
    with pytest.raises(ValidationError):
        TypeAdapter(domain("primitive").NonNegativeInt).validate_python(value)


def test_timestamp_hash_and_finite_json_validation():
    p = domain("primitive")
    stamp = TypeAdapter(p.UtcTimestamp)
    assert stamp.validate_python(STAMP) == STAMP
    for bad in ("2026-02-30T00:00:00Z", "2026-10-23", "2026-10-23T00:00:00+01:00", 1):
        with pytest.raises(ValidationError):
            stamp.validate_python(bad)
    assert (
        p.sha256_bytes(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    for bad in ("A" * 64, "a" * 63, 1):
        with pytest.raises(ValidationError):
            TypeAdapter(p.Sha256).validate_python(bad)
    for bad in (float("nan"), float("inf"), -float("inf")):
        with pytest.raises(ValidationError):
            TypeAdapter(p.FiniteNumber).validate_python(bad)
        with pytest.raises(ValidationError):
            domain("contracts").CreationInput.model_validate(
                creation(provider_params={"nested": [bad]})
            )


def creation(**overrides):
    return {
        "schema_version": 1,
        "operation": "generate",
        "session": "demo",
        "prompt": "poster",
        **overrides,
    }


def test_creation_examples_and_result_examples_match_contracts():
    c = domain("contracts")
    examples = json.loads((ROOT / "tests/fixtures/contracts/examples.json").read_text())
    for value in examples["creation_inputs"]:
        assert c.CreationInput.model_validate(value).model_dump(exclude_unset=True) == value
    for value in examples["invalid_creation_inputs"]:
        with pytest.raises(ValidationError):
            c.CreationInput.model_validate(value)
    for value in examples["results"]:
        assert c.Result.model_validate(value).model_dump(mode="json") == value
    for stream in examples["jsonl_invocations"]:
        for value in stream:
            assert (
                c.InvocationEvent.model_validate(value).model_dump(mode="json", exclude_unset=True)
                == value
            )


@pytest.mark.parametrize(
    "patch",
    [
        {"schema_version": True},
        {"count": 0},
        {"count": 101},
        {"count": "1"},
        {"prompt": "   "},
        {"extra": 1},
        {"operation": "edit"},
        {"inputs": ["https://example.invalid/a.png"]},
        {"inputs": ["file:a"] * 101},
        {"retry_of_job_id": identifier("job")},
        {"ack_duplicate_cost": True},
        {"retry_of_job_id": identifier("job"), "ack_duplicate_cost": False},
        {"profile": None},
        {"inputs": None},
        {"provider_params": {"x": {1: "bad key"}}},
    ],
)
def test_creation_rejects_invalid_boundary_inputs(patch):
    with pytest.raises(ValidationError):
        domain("contracts").CreationInput.model_validate(creation(**patch))


def test_explicit_retry_and_edit_are_valid():
    c = domain("contracts")
    value = c.CreationInput.model_validate(
        creation(
            operation="edit",
            inputs=["file:input.png"],
            retry_of_job_id=identifier("job"),
            ack_duplicate_cost=True,
        )
    )
    assert value.count == 1


def test_result_consistency_exit_codes_and_error_shape():
    c = domain("contracts")
    error = {
        "type": "outcome_unknown",
        "message": "uncertain",
        "retryable": False,
        "accepted_maybe": True,
        "next_actions": [],
    }
    value = {"schema_version": 1, "ok": False, "status": "unknown", "data": {}, "error": error}
    assert c.Result.model_validate(value).exit_code == 6
    for patch in (
        {"ok": True},
        {"error": None},
        {"status": "succeeded"},
        {"ok": 0},
        {"schema_version": True},
        {"extra": 1},
    ):
        with pytest.raises(ValidationError):
            c.Result.model_validate({**value, **patch})
    for status, code in (("succeeded", 0), ("accepted", 0), ("approved", 0), ("planned", 0)):
        assert (
            c.Result.model_validate(
                {**value, "ok": True, "status": status, "error": None}
            ).exit_code
            == code
        )
    for error_patch in (
        {"extra": 1},
        {"message": ""},
        {"retryable": 1},
        {"next_actions": [{"command": "x", "creates_paid_request": "false"}]},
    ):
        with pytest.raises(ValidationError):
            c.Result.model_validate({**value, "error": {**error, **error_patch}})


def test_event_terminal_and_job_constraints():
    c = domain("contracts")
    base = {
        "schema_version": 1,
        "seq": 1,
        "event": "started",
        "timestamp": STAMP,
        "job_id": None,
        "data": {},
    }
    result = {"schema_version": 1, "ok": True, "status": "succeeded", "data": {}, "error": None}
    for patch in (
        {"seq": True},
        {"seq": 0},
        {"event": "terminal"},
        {"result": result},
        {"result": None},
        {"event": "job_updated"},
        {"event": "artifact_saved"},
    ):
        with pytest.raises(ValidationError):
            c.InvocationEvent.model_validate({**base, **patch})
    assert c.InvocationEvent.model_validate(
        {**base, "event": "terminal", "result": result}
    ).result.ok
    assert (
        c.InvocationEvent.model_validate(
            {**base, "event": "job_updated", "job_id": identifier("job")}
        ).seq
        == 1
    )


def entity_values():
    return {
        "Session": dict(
            id=identifier("ses"), name="demo", intent="poster", version=0, created_at=STAMP
        ),
        "Turn": dict(
            id=identifier("trn"),
            session_id=identifier("ses"),
            instruction="poster",
            operation="generate",
            resolved_profile_snapshot={},
            model="model",
            parameters={},
            input_manifest=[],
            expected_session_version=0,
            job_id=identifier("job"),
            creation_state="queued",
            publication_state="not_attempted",
            created_at=STAMP,
        ),
        "Artifact": dict(
            id=identifier("art"),
            media_type="image/png",
            width=1,
            height=1,
            byte_size=3,
            sha256=HASH,
            managed_relpath="assets/a.png",
            save_state="published",
            origin_manifest={"source": "file:source.png"},
            created_at=STAMP,
        ),
        "ArtifactReference": dict(
            session_id=identifier("ses"),
            turn_id=identifier("trn"),
            artifact_id=identifier("art"),
            role="input",
            index=0,
        ),
        "Job": dict(
            id=identifier("job"),
            turn_id=identifier("trn"),
            state="queued",
            dispatch_state="not_started",
            remote_state="not_submitted",
            local_save_state="not_started",
            requested_count=1,
            actual_count=0,
            accepted_maybe=False,
            created_at=STAMP,
            updated_at=STAMP,
        ),
        "Batch": dict(
            id=identifier("bat"),
            child_job_ids=[identifier("job")],
            concurrency=1,
            summary_state="running",
            created_at=STAMP,
        ),
        "ProviderProfile": dict(
            id="profile",
            name="demo",
            provider="openai",
            family="images",
            base_url="https://example.invalid/v1",
            model_default="model",
            credential_ref="env:TEST_TOKEN",
            trust_policy={},
            revision=1,
        ),
        "CapabilityRecord": dict(
            id="cap",
            provider="openai",
            family="images",
            model="model",
            model_revision="1",
            operation="generate",
            support_status="I",
            parameter_schema={},
            semantic_mapping={},
            input_limits={},
            transport_kind="sync",
            continuation_strategy="none",
            idempotency_contract={},
            cancel_contract={},
            sources=[
                {"url": "https://example.invalid/docs", "date": "2026-10-23", "locator": "Images"}
            ],
            checked_at=STAMP,
            offline_evidence=[],
            live_evidence=[],
            digest=HASH,
        ),
        "NativeContext": dict(
            id=identifier("ctx"),
            provider="gemini",
            family="images",
            profile_identity="profile:1",
            model="model",
            format_version=1,
            private_relpath="contexts/a.json",
            sha256=HASH,
            availability="available",
            source_turn_id=identifier("trn"),
        ),
        "ExecutionPlan": dict(
            id=identifier("pln"),
            session_id=identifier("ses"),
            expected_session_version=0,
            target_snapshot={},
            operation="generate",
            model="model",
            normalized_parameters={},
            prompt_snapshot="poster",
            input_hashes=[HASH],
            portable_context={},
            capability_digest=HASH,
            context_loss=[],
            plan_digest=HASH,
            created_at=STAMP,
            expires_at="2026-10-24T00:00:00Z",
        ),
        "ApprovalContext": dict(
            id=identifier("apr"),
            plan_id=identifier("pln"),
            plan_digest=HASH,
            state="pending",
            expires_at="2026-10-24T00:00:00Z",
        ),
        "ExecutionEvent": dict(
            id=identifier("evt"),
            job_id=identifier("job"),
            job_seq=1,
            event_type="progress",
            timestamp=STAMP,
            safe_payload={},
        ),
        "Worker": dict(
            id=identifier("wrk"),
            pid=1,
            boot_identity="boot",
            state="running",
            heartbeat_at=STAMP,
            start_nonce="nonce",
        ),
    }


@pytest.mark.parametrize("name", list(entity_values()))
def test_entities_validate_and_reject_unknown_fields(name):
    model = getattr(domain("models"), name)
    values = entity_values()[name]
    assert model.model_validate(values).model_dump(mode="json", exclude_unset=True) == values
    with pytest.raises(ValidationError):
        model.model_validate({**values, "unexpected": 1})


@pytest.mark.parametrize(
    "name,patch",
    [
        ("Session", {"version": True}),
        ("Session", {"current_turn_id": identifier("job")}),
        ("Turn", {"parent_turn_id": identifier("trn")}),
        ("Job", {"state": "partial_success"}),
        ("Job", {"requested_count": 0}),
        ("Job", {"actual_count": True}),
        ("Batch", {"concurrency": 0}),
        ("Batch", {"child_job_ids": [identifier("job")] * 2}),
        ("Artifact", {"managed_relpath": "../escape.png"}),
        ("Artifact", {"managed_relpath": "/tmp/a.png"}),
        ("Artifact", {"managed_relpath": "C:\\escape.png"}),
        ("Artifact", {"width": None}),
        ("Artifact", {"width": 0}),
        ("Artifact", {"origin_manifest": {}}),
        ("NativeContext", {"private_relpath": "contexts/../secret"}),
        ("CapabilityRecord", {"support_status": "T"}),
        ("CapabilityRecord", {"sources": []}),
        ("CapabilityRecord", {"sources": [{"url": "bad", "date": "2026-02-30", "locator": ""}]}),
        ("ApprovalContext", {"state": "consumed"}),
        ("ApprovalContext", {"state": "approved"}),
        ("ExecutionPlan", {"expires_at": STAMP}),
        ("ProviderProfile", {"base_url": "file:/tmp/a"}),
        ("ProviderProfile", {"base_url": "https://user:password@example.invalid"}),
        ("ExecutionEvent", {"job_seq": 0}),
        ("Worker", {"pid": True}),
    ],
)
def test_entity_local_invariants(name, patch):
    with pytest.raises(ValidationError):
        getattr(domain("models"), name).model_validate({**entity_values()[name], **patch})


def test_plans_are_frozen_and_relationship_checks_preserve_current_success():
    m = domain("models")
    values = entity_values()
    plan = m.ExecutionPlan.model_validate(values["ExecutionPlan"])
    with pytest.raises(ValidationError):
        plan.model = "other"
    session = m.Session.model_validate({**values["Session"], "current_turn_id": identifier("trn")})
    turn = m.Turn.model_validate(values["Turn"])
    with pytest.raises(ValueError):
        session.validate_current_turn(turn)
    success = m.Turn.model_validate(
        {**values["Turn"], "creation_state": "succeeded", "publication_state": "published"}
    )
    session.validate_current_turn(success)
    other = m.Turn.model_validate(
        {**values["Turn"], "session_id": "ses_22222222222242228222222222222222"}
    )
    child = m.Turn.model_validate(
        {
            **values["Turn"],
            "id": "trn_22222222222242228222222222222222",
            "parent_turn_id": identifier("trn"),
        }
    )
    with pytest.raises(ValueError):
        child.validate_parent(other)
    child.validate_parent(success)


def test_errors_have_stable_exit_mapping():
    e = domain("errors")
    for status, code in [
        ("internal_error", 1),
        ("validation_error", 2),
        ("unsupported_capability", 2),
        ("authentication_error", 3),
        ("permission_denied", 3),
        ("not_found", 4),
        ("conflict", 5),
        ("wait_timeout", 6),
        ("unknown", 6),
        ("approval_required", 6),
        ("failed", 7),
        ("save_failed", 7),
        ("canceled", 7),
        ("safety_refusal", 8),
        ("partial_success", 9),
    ]:
        assert e.exit_code_for(status) == code
    error = e.DomainError("validation_error", "invalid input")
    assert str(error) == "invalid input"
    assert error.exit_code == 2
