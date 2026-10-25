"""Wait cancellation is observation-only; unsupported termination never fakes success."""

import io
import json

import pytest

from imagen_cli.application.container import Container
from imagen_cli.cli.entrypoint import main
from tests.integration.test_approved_execution import execute, service, setup


@pytest.mark.parametrize("scope", ["wait", "local", "remote"])
def test_cancel_preserves_remote_success_and_outputs(tmp_path, png_bytes, scope):
    with Container(workspace=str(tmp_path)) as container:
        plan, _, validator, executor, adapter, _ = setup(container, png_bytes)
        result = execute(service(container, validator, executor), plan)
        identity = result["job"]["id"]
        before = container.repositories.get("jobs", identity)
        session = container.repositories.get("sessions", plan.session_id)
        stdout = io.StringIO()
        code = main([
            "job", "cancel", identity, "--scope", scope,
            "--workspace", str(tmp_path), "--output", "json", "--no-interactive",
        ], stdout=stdout, stderr=io.StringIO())
        value = json.loads(stdout.getvalue())
        if scope == "wait":
            assert code == 0
            assert value["status"] == "succeeded"
            assert value["data"]["job"] == before
            assert len(value["data"]["artifacts"]) == 1
            events = container.repositories.list("events", job_id=identity)
            assert events[-1]["event_type"] == "wait_stopped"
        else:
            assert value["status"] == "unsupported_capability"
            assert code != 0
        assert container.repositories.get("jobs", identity) == before
        assert container.repositories.get("sessions", plan.session_id) == session
        assert len(adapter.requests) == 1
        assert container.artifact_store.get(result["artifacts"][0]["id"]).read_bytes() == png_bytes


def test_wait_stop_does_not_mark_unknown_job_canceled(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        plan, _, validator, executor, adapter, _ = setup(
            container, png_bytes, error=RuntimeError("uncertain"),
        )
        result = execute(service(container, validator, executor), plan)
        status, data = container.execute(
            "job cancel", {"job_id": result["job"]["id"], "scope": "wait"},
        )
        assert status == "succeeded"
        assert data["job"]["state"] == "unknown"
        assert data["job"]["accepted_maybe"] is True
        assert data["job"].get("cancel_remote") is None
        assert data["job"].get("cancel_local") is None
        assert len(adapter.requests) == 1


def test_active_wait_stops_on_new_observation_event(tmp_path, png_bytes, monkeypatch):
    from imagen_cli.application.artifact_service import ArtifactService
    from imagen_cli.cli.commands import job

    with Container(workspace=str(tmp_path)) as container:
        plan, _, validator, executor, _, _ = setup(container, png_bytes)
        result = execute(service(container, validator, executor), plan)
        identity = result["job"]["id"]
        container.repositories.update("jobs", identity, {"state": "running"})
        artifacts = ArtifactService(container.repositories, container.artifact_store)

        def stop(_seconds):
            job.cancel_(artifacts, identity, scope="wait")

        monkeypatch.setattr("imagen_cli.cli.commands.job.time.sleep", stop)
        status, data = job.wait_(artifacts, identity, timeout=1)
        assert status == "canceled"
        assert data["job"]["state"] == "running"
        assert data["job"]["remote_state"] == "succeeded"
        assert len(data["artifacts"]) == 1


def test_old_stop_event_does_not_cancel_future_wait(tmp_path, png_bytes, monkeypatch):
    from imagen_cli.application.artifact_service import ArtifactService
    from imagen_cli.cli.commands import job

    with Container(workspace=str(tmp_path)) as container:
        plan, _, validator, executor, _, _ = setup(container, png_bytes)
        result = execute(service(container, validator, executor), plan)
        identity = result["job"]["id"]
        artifacts = ArtifactService(container.repositories, container.artifact_store)
        job.cancel_(artifacts, identity, scope="wait")
        container.repositories.update("jobs", identity, {"state": "running"})

        def finish(_seconds):
            container.repositories.update("jobs", identity, {"state": "succeeded"})

        monkeypatch.setattr("imagen_cli.cli.commands.job.time.sleep", finish)
        status, data = job.wait_(artifacts, identity, timeout=1)
        assert status == "succeeded"
        assert data["job"]["state"] == "succeeded"


def test_local_cancel_terminates_only_a_verified_executor(tmp_path, png_bytes):
    import subprocess
    import sys

    from imagen_cli.application.cancel_service import process_start
    from imagen_cli.application.container import Container
    from tests.integration.test_approved_execution import execute, service, setup

    with Container(workspace=str(tmp_path)) as container:
        plan, _, validator, executor, adapter, _ = setup(container, png_bytes)
        result = execute(service(container, validator, executor), plan)
        job_id = result["job"]["id"]
        container.repositories.update("jobs", job_id, {"state": "running"})

        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            container.repositories.update("jobs", job_id, {"executor_id": {
                "pid": child.pid, "start": process_start(child.pid),
            }})
            status, data = container.execute("job cancel", {"job_id": job_id, "scope": "local"})
            assert status == "succeeded"
            assert child.wait(timeout=10) in (-15, 15)
            assert data["job"]["state"] == "running"
            events = [row["event_type"] for row in
                      container.repositories.list("events", job_id=job_id)]
            assert events[-2:] == ["cancel_intent", "cancel_local"]
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)

        container.repositories.update("jobs", job_id, {"executor_id": {
            "pid": child.pid, "start": "stale",
        }})
        status, data = container.execute("job cancel", {"job_id": job_id, "scope": "local"})
        assert status == "unsupported_capability"
        assert data["job"]["state"] == "running"


def test_local_cancel_without_identity_is_refused(tmp_path, png_bytes):
    from imagen_cli.application.container import Container
    from tests.integration.test_approved_execution import execute, service, setup

    with Container(workspace=str(tmp_path)) as container:
        plan, _, validator, executor, _, _ = setup(container, png_bytes)
        result = execute(service(container, validator, executor), plan)
        job_id = result["job"]["id"]
        container.repositories.update("jobs", job_id, {"state": "running", "executor_id": None})
        status, _ = container.execute("job cancel", {"job_id": job_id, "scope": "local"})
        assert status == "unsupported_capability"
        assert container.repositories.get("jobs", job_id)["state"] == "running"


def test_local_cancel_race_is_not_reported_as_canceled(tmp_path, png_bytes, monkeypatch):
    import os as os_module

    from imagen_cli.application.cancel_service import process_start
    from imagen_cli.application.container import Container
    from tests.integration.test_approved_execution import execute, service, setup

    with Container(workspace=str(tmp_path)) as container:
        plan, _, validator, executor, _, _ = setup(container, png_bytes)
        job_id = execute(service(container, validator, executor), plan)["job"]["id"]
        container.repositories.update("jobs", job_id, {
            "state": "running",
            "executor_id": {"pid": os_module.getpid(), "start": process_start(os_module.getpid())},
        })

        def gone(pid, sig):
            raise ProcessLookupError

        monkeypatch.setattr("imagen_cli.application.cancel_service.os.kill", gone)
        status, data = container.execute("job cancel", {"job_id": job_id, "scope": "local"})
        assert status == "conflict"
        assert data["job"]["state"] == "running"
        events = [row["event_type"] for row in
                  container.repositories.list("events", job_id=job_id)]
        assert events[-2:] == ["cancel_intent", "cancel_local_failed"]
