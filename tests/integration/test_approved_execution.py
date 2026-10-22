"""Approved execution through real SQLite, managed files and foreground execution."""

import hashlib
import sqlite3

import pytest

from imagen_cli.application.artifact_service import ArtifactService
from imagen_cli.application.container import Container
from imagen_cli.application.outcome_service import OutcomeService
from imagen_cli.application.planning_service import PlanningService
from imagen_cli.application.session_service import SessionService
from imagen_cli.domain.primitive import new_id
from imagen_cli.execution.foreground import ForegroundExecutor
from imagen_cli.providers.base import ArtifactDescriptor, ImmediateResult
from tests.support import capability


class OfflineAdapter:
    def __init__(self, content, *, error=None, observe=None):
        self.content = content
        self.error = error
        self.observe = observe
        self.requests = []

    def create(self, request):
        self.requests.append(request)
        if self.observe:
            self.observe(request)
        if self.error:
            raise self.error
        return ImmediateResult(1, 1, (ArtifactDescriptor(new_id("art"), 0, self.content),))


def setup(container, png_bytes, *, inputs=False, approved=True, error=None, observe=None,
          revision="2026-01", context=None):
    repo = container.repositories
    session = SessionService(repo, container.artifact_store).create_session("work")
    profile = {
        "id": "offline", "name": "offline", "provider": "openai", "family": "images",
        "base_url": "https://offline.example.test/v1", "revision": 1,
        "model_default": "image-model", "credential_ref": "fixture-not-resolved",
        "trust_policy": {"allowed_upload_targets": ["https://offline.example.test/v1"]},
    }
    repo.insert("profiles", profile)
    record = capability(
        parameter_schema={"type": "object", "properties": {"count": {"const": 1}},
                          "additionalProperties": False},
        continuation_strategy="explicit_images" if inputs else "none",
    )
    validator = None
    ids = ()
    if inputs:
        identity = new_id("art")
        container.artifact_store.stage(identity, png_bytes)
        container.artifact_store.publish(identity)
        ids = (identity,)
    target = {
        "profile_identity": "offline", "profile_revision": 1,
        "endpoint_origin": profile["base_url"],
        "allowed_upload_targets": [profile["base_url"]],
        "provider": "openai", "family": "images", "model_revision": revision,
    }
    planning = PlanningService(repo)
    plan, approval = planning.create_plan(
        session_id=session.id, target_snapshot=target, operation="generate",
        model="image-model", normalized_parameters={"count": 1}, prompt_snapshot="red kite",
        input_hashes=[hashlib.sha256(png_bytes).hexdigest()] if inputs else [],
        portable_context=context or ({"kind": "explicit_images", "input_artifact_ids": list(ids),
                          "mask_artifact_id": None} if inputs else {"kind": "none"}),
        capability_digest=record["digest"],
    )
    if approved:
        planning.approve(plan.id, plan.plan_digest)
    adapter = OfflineAdapter(png_bytes, error=error, observe=observe)
    executor = ForegroundExecutor(
        repo, adapter, ArtifactService(repo, container.artifact_store),
        OutcomeService(repo), container.private_state_store,
    )
    return plan, approval, validator, executor, adapter, ids


def service(container, validator, executor):
    from imagen_cli.application.execution_service import ExecutionService

    return ExecutionService(
        container.repositories, container.artifact_store, executor,
    )


def execute(api, plan, ids=(), **kwargs):
    return api.execute_approved(
        plan.id, plan.plan_digest, model_revision="2026-01", input_artifact_ids=ids, **kwargs,
    )


def test_approved_execution_commits_once_publishes_and_repeats_read_only(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        def observe(request):
            with Container(workspace=str(tmp_path)) as other:
                assert other.repositories.get("jobs", request.job_id)["dispatch_state"] == (
                    "intent_recorded"
                )
                assert other.repositories.list("approvals")[0]["state"] == "consumed"
            assert request.authorized and request.dispatch_intent_recorded
            assert request.inputs == (png_bytes,)

        plan, approval, validator, executor, adapter, ids = setup(
            container, png_bytes, inputs=True, observe=observe,
        )
        api = service(container, validator, executor)
        result = execute(api, plan, ids)
        assert result["status"] == "succeeded"
        assert result["exit_code"] == 0
        assert result["job"]["plan_id"] == plan.id
        assert container.repositories.get("sessions", plan.session_id)["current_turn_id"] == (
            result["turn"]["id"]
        )
        assert container.artifact_store.get(result["artifacts"][0]["id"]).read_bytes() == png_bytes
        assert container.repositories.get("approvals", approval.id)["execution_job_id"] == (
            result["job"]["id"]
        )
        before = container.connection.total_changes
        assert execute(api, plan, ids) == result
        assert container.connection.total_changes == before
        assert len(adapter.requests) == 1
        assert len(container.repositories.list("turns")) == 1
        refs = container.repositories.list("artifact_references", role="input")
        assert refs[0]["artifact_id"] == ids[0]


def test_consumed_unknown_returns_facts_even_if_sources_disappear(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        plan, _, validator, executor, adapter, ids = setup(
            container, png_bytes, inputs=True, error=RuntimeError("uncertain"),
        )
        api = service(container, validator, executor)
        result = execute(api, plan, ids)
        assert result["status"] == "unknown"
        assert result["exit_code"] == 6
        assert result["job"]["accepted_maybe"] is True
        container.artifact_store.get(ids[0]).unlink()
        container.repositories.update("profiles", "offline", {"revision": 2})
        assert execute(api, plan) == result
        assert len(adapter.requests) == 1


@pytest.mark.parametrize("change", ["pending", "profile", "endpoint", "upload", "provider",
                                   "revision", "bytes", "order", "version",
                                   "parent", "digest", "context"])
def test_drift_or_missing_approval_never_consumes_or_dispatches(tmp_path, png_bytes, change):
    with Container(workspace=str(tmp_path)) as container:
        plan, approval, validator, executor, adapter, ids = setup(
            container, png_bytes, inputs=True, approved=change != "pending",
            revision="other" if change == "revision" else "2026-01",
            context={"kind": "native_parts"} if change == "context" else None,
        )
        repo = container.repositories
        if change in {"profile", "endpoint", "upload", "provider"}:
            changes = {"profile": {"revision": 2},
                       "endpoint": {"base_url": "https://other.example.test"},
                       "upload": {"trust_policy": {"allowed_upload_targets": []}},
                       "provider": {"provider": "other"}}[change]
            repo.update("profiles", "offline", changes)
        elif change == "bytes":
            container.artifact_store.get(ids[0]).write_bytes(b"tampered")
        elif change == "order":
            ids = ()
        elif change == "version":
            repo.update("sessions", plan.session_id, {"version": 1})
        elif change == "parent":
            repo.insert("turns", {"id": "trn_parent", "session_id": plan.session_id,
                                  "creation_state": "succeeded"})
            repo.update("sessions", plan.session_id, {"current_turn_id": "trn_parent"})
        elif change == "digest":
            plan = plan.model_copy(update={"plan_digest": "f" * 64})
        with pytest.raises((ValueError, KeyError, OSError)):
            execute(service(container, validator, executor), plan, ids)
        assert adapter.requests == []
        assert repo.list("jobs") == []
        assert repo.get("approvals", approval.id)["state"] == (
            "pending" if change == "pending" else "approved"
        )


def test_job_insert_failure_rolls_back_consumption_and_turn(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        plan, approval, validator, executor, adapter, ids = setup(container, png_bytes)
        container.connection.execute(
            "CREATE TRIGGER fail_job BEFORE INSERT ON jobs "
            "BEGIN SELECT RAISE(ABORT,'injected'); END"
        )
        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            execute(service(container, validator, executor), plan, ids)
        assert container.repositories.get("approvals", approval.id)["state"] == "approved"
        assert container.repositories.list("turns") == []
        assert container.repositories.list("jobs") == []
        assert adapter.requests == []


def test_competing_invocation_during_create_only_observes_existing_job(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        observed = []

        def observe(request):
            with Container(workspace=str(tmp_path)) as other:
                other_adapter = OfflineAdapter(png_bytes)
                other_executor = ForegroundExecutor(
                    other.repositories, other_adapter,
                    ArtifactService(other.repositories, other.artifact_store),
                    OutcomeService(other.repositories), other.private_state_store,
                )
                value = execute(service(other, validator, other_executor), plan)
                observed.append(value)
                assert value["status"] == "unknown"
                assert value["job"]["id"] == request.job_id
                assert other_adapter.requests == []

        plan, _, validator, executor, adapter, _ = setup(container, png_bytes, observe=observe)
        result = execute(service(container, validator, executor), plan)
        assert result["status"] == "succeeded"
        assert len(observed) == 1
        assert len(adapter.requests) == 1
        assert len(container.repositories.list("jobs")) == 1


def test_consumed_before_dispatch_is_not_automatically_restarted(tmp_path, png_bytes, monkeypatch):
    with Container(workspace=str(tmp_path)) as container:
        plan, _, validator, executor, adapter, _ = setup(container, png_bytes)
        api = service(container, validator, executor)

        def stop(*args, **kwargs):
            raise KeyboardInterrupt

        monkeypatch.setattr(executor, "execute", stop)
        with pytest.raises(KeyboardInterrupt):
            execute(api, plan)
        result = execute(api, plan)
        assert result["status"] == "unknown"
        assert result["job"]["dispatch_state"] == "not_started"
        assert result["job"]["state"] == "queued"
        assert adapter.requests == []
        assert len(container.repositories.list("jobs")) == 1


def test_caller_authorized_flag_is_not_an_execution_proof(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        plan, approval, validator, executor, adapter, _ = setup(
            container, png_bytes, approved=False,
        )
        api = service(container, validator, executor)
        with pytest.raises(TypeError):
            execute(api, plan, authorized=True)
        assert container.repositories.get("approvals", approval.id)["state"] == "pending"
        assert container.repositories.list("jobs") == []
        assert adapter.requests == []


def test_consumed_approval_digest_tampering_is_not_accepted(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        plan, approval, validator, executor, adapter, _ = setup(container, png_bytes)
        api = service(container, validator, executor)
        execute(api, plan)
        container.connection.execute(
            "UPDATE approvals SET payload=json_set(payload,'$.plan_digest',?) WHERE id=?",
            ("f" * 64, approval.id),
        )
        with pytest.raises(ValueError, match="digest"):
            execute(api, plan)
        assert len(adapter.requests) == 1


def test_mask_cannot_replace_identical_input_bytes_without_approved_role(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        plan, approval, validator, executor, adapter, ids = setup(container, png_bytes, inputs=True)
        with pytest.raises(ValueError, match="snapshot"):
            execute(service(container, validator, executor), plan, mask_artifact_id=ids[0])
        assert container.repositories.get("approvals", approval.id)["state"] == "approved"
        assert container.repositories.list("jobs") == []
        assert adapter.requests == []
