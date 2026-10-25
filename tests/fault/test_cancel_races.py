"""Wait stop, local termination, remote refusal and races stay distinguishable."""

import os
import subprocess
import sys

from imagen_cli.application.cancel_service import process_start
from imagen_cli.application.container import Container
from tests.integration.test_approved_execution import execute, service, setup


def _running_job(container, png_bytes):
    plan, _, validator, executor, _, _ = setup(container, png_bytes)
    job_id = execute(service(container, validator, executor), plan)["job"]["id"]
    container.repositories.update("jobs", job_id, {"state": "running"})
    return job_id


def test_wait_stop_only_stops_the_observer(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        job_id = _running_job(container, png_bytes)
        before = container.repositories.get("jobs", job_id)
        status, _ = container.execute("job cancel", {"job_id": job_id, "scope": "wait"})
        assert status == "succeeded"
        after = container.repositories.get("jobs", job_id)
        assert {key: after[key] for key in before} == before
        assert [row["event_type"] for row in
                container.repositories.list("events", job_id=job_id)][-1] == "wait_stopped"


def test_local_termination_requires_a_matching_executor(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        job_id = _running_job(container, png_bytes)
        container.repositories.update("jobs", job_id, {"executor_id": {
            "pid": os.getpid(), "start": "0",
        }})
        status, data = container.execute("job cancel", {"job_id": job_id, "scope": "local"})
        assert status == "unsupported_capability"
        assert data["job"]["state"] == "running"


def test_remote_cancel_is_refused_before_any_request(tmp_path, png_bytes, monkeypatch):
    with Container(workspace=str(tmp_path)) as container:
        job_id = _running_job(container, png_bytes)

        def forbidden(*args, **kwargs):
            raise AssertionError("remote cancel must not send")

        monkeypatch.setattr("imagen_cli.application.cancel_service.os.kill", forbidden)
        status, data = container.execute("job cancel", {"job_id": job_id, "scope": "remote"})
        assert status == "unsupported_capability"
        assert data["job"]["state"] == "running"


def test_terminated_executor_is_sigterm_not_a_faked_cancellation(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        job_id = _running_job(container, png_bytes)
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            container.repositories.update("jobs", job_id, {"executor_id": {
                "pid": child.pid, "start": process_start(child.pid),
            }})
            status, data = container.execute(
                "job cancel", {"job_id": job_id, "scope": "local"},
            )
            assert status == "succeeded"
            assert child.wait(timeout=10) in (-15, 15)
            assert data["job"]["state"] == "running"
            assert data["job"]["remote_state"] == "succeeded"
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
