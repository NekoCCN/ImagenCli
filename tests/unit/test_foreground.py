"""Offline foreground execution with deterministic mock adapters."""

import json
from dataclasses import replace

import pytest

from imagen_cli.application.artifact_service import ArtifactService
from imagen_cli.application.container import Container
from imagen_cli.application.outcome_service import OutcomeService
from imagen_cli.providers.base import ArtifactDescriptor, ImmediateResult, ValidatedRequest


def setup_work(container):
    repo = container.repositories
    repo.insert("sessions", {"id": "ses_one", "name": "one"})
    repo.insert("turns", {"id": "trn_one", "session_id": "ses_one", "expected_session_version": 0})
    repo.insert("jobs", {"id": "job_one", "turn_id": "trn_one", "requested_count": 1})
    repo.update("turns", "trn_one", {"job_id": "job_one"})


def request():
    return ValidatedRequest(
        plan_id="pln_one",
        job_id="job_one",
        profile_identity="offline",
        profile_revision=1,
        operation="generate",
        model="offline",
        model_revision="1",
        prompt="red square",
        normalized_parameters={},
        capability_digest="a" * 64,
        authorized=True,
        dispatch_intent_recorded=True,
    )


class Adapter:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, 0

    def create(self, value):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def executor(container, adapter):
    from imagen_cli.execution.foreground import ForegroundExecutor

    return ForegroundExecutor(
        container.repositories,
        adapter,
        ArtifactService(container.repositories, container.artifact_store),
        OutcomeService(container.repositories),
        container.private_state_store,
    )


def test_create_persists_private_response_then_publishes_output(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        setup_work(container)
        adapter = Adapter(
            ImmediateResult(
                1,
                1,
                (ArtifactDescriptor("art_one", 0, png_bytes),),
                private_response={"opaque": "native"},
            )
        )
        result = executor(container, adapter).execute(request())
        assert result["status"] == "succeeded"
        assert adapter.calls == 1
        assert (
            container.repositories.get("jobs", "job_one")["dispatch_state"] == "response_received"
        )
        private = container.repositories.get("jobs", "job_one", include_private=True)
        locator = json.loads(private["private_locator_ref"])
        assert json.loads(container.private_state_store.read("job_one", locator["sha256"])) == {
            "opaque": "native"
        }
        assert container.artifact_store.get("art_one").read_bytes() == png_bytes


def test_remote_execution_reports_phases_without_fake_percentages(tmp_path, png_bytes):
    from imagen_cli.execution.foreground import ForegroundExecutor

    with Container(workspace=str(tmp_path)) as container:
        setup_work(container)
        events = []
        adapter = Adapter(ImmediateResult(1, 1, (ArtifactDescriptor('art_one', 0, png_bytes),)))
        runner = ForegroundExecutor(
            container.repositories, adapter,
            ArtifactService(container.repositories, container.artifact_store),
            OutcomeService(container.repositories), container.private_state_store,
            progress=events.append,
        )
        result = runner.execute(request())
        assert result['status'] == 'succeeded'
        assert [event['stage'] for event in events] == ['running', 'saving', 'completed']
        assert all(event['total'] is None for event in events[:2])


def test_existing_intent_never_calls_create_again(tmp_path):
    with Container(workspace=str(tmp_path)) as container:
        setup_work(container)
        container.repositories.update("jobs", "job_one", {"dispatch_state": "intent_recorded"})
        adapter = Adapter()
        with pytest.raises(ValueError, match="already dispatched"):
            executor(container, adapter).execute(request())
        assert adapter.calls == 0


@pytest.mark.parametrize("error", [KeyboardInterrupt(), RuntimeError("disconnected")])
def test_interrupt_or_uncertain_failure_is_not_retried(tmp_path, error):
    with Container(workspace=str(tmp_path)) as container:
        setup_work(container)
        adapter = Adapter(error=error)
        result = executor(container, adapter).execute(request())
        assert result["status"] == "unknown"
        assert adapter.calls == 1
        job = container.repositories.get("jobs", "job_one")
        assert job["state"] == "unknown"
        assert job["accepted_maybe"] is True
        assert job["dispatch_state"] == "intent_recorded"


def test_private_response_is_durable_before_response_fact_and_artifacts(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        setup_work(container)
        adapter = Adapter(
            ImmediateResult(
                1,
                1,
                (ArtifactDescriptor("art_one", 0, png_bytes),),
                private_response={"parts": [{"signature": "opaque"}]},
            )
        )
        api = executor(container, adapter)
        original = api.artifact_service.save

        def save(*args, **kwargs):
            job = container.repositories.get("jobs", "job_one", include_private=True)
            ref = json.loads(job["private_locator_ref"])
            assert job["dispatch_state"] == "response_received"
            assert json.loads(container.private_state_store.read("job_one", ref["sha256"])) == {
                "parts": [{"signature": "opaque"}]
            }
            return original(*args, **kwargs)

        api.artifact_service.save = save
        result = api.execute(replace(request(), dispatch_intent_recorded=False))
        assert result["status"] == "succeeded"
        assert "opaque" not in json.dumps(result)
        assert "private_locator_ref" not in container.repositories.get("jobs", "job_one")


@pytest.mark.parametrize("boundary", ["file", "database"])
def test_response_persistence_failure_preserves_intent_and_never_resends(
    tmp_path, png_bytes, monkeypatch, boundary
):
    with Container(workspace=str(tmp_path)) as container:
        setup_work(container)
        adapter = Adapter(ImmediateResult(1, 1, (ArtifactDescriptor("art_one", 0, png_bytes),)))
        api = executor(container, adapter)
        if boundary == "file":
            monkeypatch.setattr(
                api.private_state_store,
                "write",
                lambda *a: (_ for _ in ()).throw(OSError("secret disk failure")),
            )
        else:
            original = container.repositories.update

            def update(table, identity, changes):
                if changes.get("dispatch_state") == "response_received":
                    raise OSError("secret database failure")
                return original(table, identity, changes)

            monkeypatch.setattr(container.repositories, "update", update)
        result = api.execute(request())
        assert result["status"] == "save_failed"
        assert "secret" not in json.dumps(result)
        job = container.repositories.get("jobs", "job_one")
        assert job["remote_state"] == "succeeded"
        assert job["local_save_state"] == "failed"
        assert result["next_actions"] == ["fetch", "reconcile"]
        assert job["dispatch_state"] == "intent_recorded"
        assert container.repositories.list("artifacts") == []
        with pytest.raises(ValueError, match="already dispatched"):
            api.execute(request())
        assert adapter.calls == 1


def test_authorization_and_count_validation_do_not_claim_intent(tmp_path):
    with Container(workspace=str(tmp_path)) as container:
        setup_work(container)
        adapter = Adapter()
        api = executor(container, adapter)
        with pytest.raises(ValueError):
            api.execute(replace(request(), authorized=False))
        for count in [True, 0, -1, 2]:
            with pytest.raises(ValueError):
                api.execute(request(), count)
        assert adapter.calls == 0
        assert container.repositories.get("jobs", "job_one")["dispatch_state"] == "not_started"


def test_save_failure_keeps_remote_success_and_no_create_retry(tmp_path):
    with Container(workspace=str(tmp_path)) as container:
        setup_work(container)
        adapter = Adapter(ImmediateResult(1, 1, (ArtifactDescriptor("art_bad", 0, b"bad"),)))
        api = executor(container, adapter)
        assert api.execute(request())["status"] == "save_failed"
        assert container.repositories.get("jobs", "job_one")["remote_state"] == "succeeded"
        with pytest.raises(ValueError, match="already dispatched"):
            api.execute(request())
        assert adapter.calls == 1


def test_explicit_provider_rejection_is_safe_and_not_retried(tmp_path):
    from imagen_cli.providers.base import ProviderFailure

    with Container(workspace=str(tmp_path)) as container:
        setup_work(container)
        adapter = Adapter(error=ProviderFailure("authentication_error", "secret response"))
        result = executor(container, adapter).execute(request())
        assert result["status"] == "failed"
        assert adapter.calls == 1


def test_private_response_restore_verifies_locator_and_content(tmp_path):
    with Container(workspace=str(tmp_path)) as container:
        setup_work(container)
        api = executor(container, Adapter(ImmediateResult(1, 0, (), private_response={"x": [1]})))
        api.execute(request())
        assert api.read_private_response("job_one") == {"x": [1]}
        row = container.repositories.get("jobs", "job_one", include_private=True)
        ref = json.loads(row["private_locator_ref"])
        container.repositories.update(
            "jobs",
            "job_one",
            {"private_locator_ref": json.dumps({**ref, "private_relpath": "../escape.bin"})},
        )
        with pytest.raises(ValueError):
            api.read_private_response("job_one")
        container.repositories.update("jobs", "job_one", {"private_locator_ref": json.dumps(ref)})
        (container.private_state_store.root / ref["private_relpath"]).write_bytes(b"tampered")
        with pytest.raises(ValueError):
            api.read_private_response("job_one")


def test_intent_commits_before_create_and_competing_executor_cannot_send(tmp_path):
    with Container(workspace=str(tmp_path)) as first, Container(workspace=str(tmp_path)) as second:
        setup_work(first)
        loser = Adapter()
        other = executor(second, loser)

        class Winner:
            def create(self, value):
                assert value.dispatch_intent_recorded is True
                assert (
                    second.repositories.get("jobs", "job_one")["dispatch_state"]
                    == "intent_recorded"
                )
                with pytest.raises(ValueError, match="already dispatched"):
                    other.execute(request())
                return ImmediateResult(1, 0, ())

        executor(first, Winner()).execute(replace(request(), dispatch_intent_recorded=False))
        assert loser.calls == 0


@pytest.mark.parametrize("state", ["succeeded", "failed", "canceled", "unknown", "running"])
def test_nonqueued_job_cannot_create_even_without_intent(tmp_path, state):
    with Container(workspace=str(tmp_path)) as container:
        setup_work(container)
        container.repositories.update("jobs", "job_one", {"state": state})
        adapter = Adapter()
        with pytest.raises(ValueError):
            executor(container, adapter).execute(request())
        assert adapter.calls == 0


@pytest.mark.parametrize("counts", [(2, 1), (1, 0), (1, 2), (True, 1), (1, True)])
def test_inconsistent_adapter_counts_cannot_publish(tmp_path, png_bytes, counts):
    with Container(workspace=str(tmp_path)) as container:
        setup_work(container)
        adapter = Adapter(ImmediateResult(*counts, (ArtifactDescriptor("art_one", 0, png_bytes),)))
        result = executor(container, adapter).execute(request())
        assert result["status"] != "succeeded"
        assert container.repositories.list("artifacts") == []
        assert container.repositories.get("sessions", "ses_one")["current_turn_id"] is None
        assert adapter.calls == 1


def test_intent_failure_and_outer_transaction_never_allow_create(tmp_path, monkeypatch):
    with Container(workspace=str(tmp_path)) as container:
        setup_work(container)
        adapter = Adapter()
        api = executor(container, adapter)
        with container.repositories.transaction(), pytest.raises(ValueError):
            api.execute(request())
        monkeypatch.setattr(
            container.repositories,
            "append_event",
            lambda *a: (_ for _ in ()).throw(OSError("database busy")),
        )
        with pytest.raises(OSError):
            api.execute(request())
        assert adapter.calls == 0
        assert container.repositories.get("jobs", "job_one")["dispatch_state"] == "not_started"
