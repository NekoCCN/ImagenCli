"""Isolated-process worker handshake, heartbeat, lease and recover-only takeover.

Children run with sockets disabled; the real SQLite repository is shared. No
provider call is ever made here, so a recovered job must keep its unknown or
queued dispatch facts unchanged.
"""

import multiprocessing
import os
import signal
import socket
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from imagen_cli.application.cancel_service import process_start
from imagen_cli.application.container import Container
from imagen_cli.application.session_service import SessionService
from imagen_cli.cli.options import SharedOptions
from imagen_cli.execution import worker as worker_module
from imagen_cli.execution.worker import LeaseError, Worker
from tests.contract.test_creation_planning_cli import seed
from tests.integration.test_local_cli import invoke


def _forbid_network() -> None:
    socket.socket = lambda *a, **k: (_ for _ in ()).throw(AssertionError("network forbidden"))
    socket.getaddrinfo = socket.socket


def _seed_job(workspace: str) -> str:
    with Container(workspace=workspace) as container:
        sessions = SessionService(container.repositories, container.artifact_store)
        session = sessions.create_session("worker")
        turn = sessions.create_turn(
            session.id, instruction="offline", operation="generate",
            resolved_profile_snapshot={}, model="local", parameters={},
        )
        return turn.job_id


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 24, tzinfo=UTC)

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _handshake_child(workspace: str, connection: Any) -> None:
    _forbid_network()
    from imagen_cli.application.container import Container
    from imagen_cli.execution.worker import Worker

    with Container(workspace=workspace) as container:
        worker = Worker(container.repositories, heartbeat_seconds=1, lease_seconds=30)
        started = worker.start()
        first = started["heartbeat_at"]
        time.sleep(0.05)
        worker.heartbeat()
        row = container.repositories.get("workers", worker.worker_id)
        worker.stop()
        connection.send({
            "id": worker.worker_id,
            "pid": row["pid"],
            "boot_identity": row["boot_identity"],
            "start_nonce": row["start_nonce"],
            "first": first,
            "second": row["heartbeat_at"],
            "state": container.repositories.get("workers", worker.worker_id)["state"],
        })
    connection.close()


def test_isolated_process_startup_handshake_and_heartbeat(tmp_path: Path) -> None:
    workspace = str(tmp_path / "workspace")
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_handshake_child, args=(workspace, child))
    process.start()
    child.close()
    try:
        assert parent.poll(10), "worker child did not report"
        facts = parent.recv()
        process.join(10)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.kill()
            process.join(5)
    assert facts["pid"] == process.pid
    assert facts["boot_identity"]
    assert facts["start_nonce"]
    assert facts["second"] > facts["first"]
    assert facts["state"] == "stopped"


def test_expired_lease_is_recover_only_and_fences_the_old_owner(tmp_path: Path) -> None:
    workspace = str(tmp_path / "workspace")
    identity = _seed_job(workspace)
    clock = _Clock()
    with Container(workspace=workspace) as container:
        repositories = container.repositories
        first = Worker(repositories, clock=lambda: clock.now, lease_seconds=30)
        first.start()
        token = first.claim(identity)
        claimed = repositories.get("jobs", identity, include_private=True)
        assert claimed["lease_token"] == token
        assert claimed["dispatch_state"] == "not_started"
        assert claimed["state"] == "queued"

        clock.advance(31)
        second = Worker(repositories, clock=lambda: clock.now, lease_seconds=30)
        second.start()
        with pytest.raises(LeaseError):
            second.claim(identity)
        with pytest.raises(LeaseError):
            first.fenced_update(identity, {"accepted_maybe": True}, event="stale_write")

        recovered = second.recover(identity)
        assert len(recovered) == 1
        row = recovered[0]
        assert row["mode"] == "recover_only"
        assert row["creates_paid_request"] is False
        job = repositories.get("jobs", identity, include_private=True)
        assert job["dispatch_state"] == "not_started"
        assert job["state"] == "queued"
        assert job["remote_state"] == "not_submitted"
        assert job["lease_token"] != token
        assert first.owns(identity) is False
        assert second.owns(identity) is True
        with pytest.raises(LeaseError):
            first.fenced_update(identity, {"accepted_maybe": True}, event="stale_write")
        second.fenced_update(identity, {"accepted_maybe": False}, event="observed")
        assert len(repositories.list("jobs")) == 1


def test_recovery_preserves_unknown_and_never_redispatches(tmp_path: Path) -> None:
    workspace = str(tmp_path / "workspace")
    identity = _seed_job(workspace)
    clock = _Clock()
    with Container(workspace=workspace) as container:
        repositories = container.repositories
        repositories.update(
            "jobs", identity,
            {
                "state": "running", "dispatch_state": "intent_recorded",
                "remote_state": "unknown", "accepted_maybe": True,
                "lease_token": "old-token",
                "lease_expires_at": (clock.now - timedelta(seconds=1)).isoformat().replace(
                    "+00:00", "Z"
                ),
            },
        )
        worker = Worker(repositories, clock=lambda: clock.now)
        worker.start()
        worker.recover(identity)
        job = repositories.get("jobs", identity, include_private=True)
        assert job["state"] == "running"
        assert job["dispatch_state"] == "intent_recorded"
        assert job["remote_state"] == "unknown"
        assert job["accepted_maybe"] is True
        assert job["lease_token"] != "old-token"


def test_active_lease_cannot_be_recovered_or_claimed_twice(tmp_path: Path) -> None:
    workspace = str(tmp_path / "workspace")
    identity = _seed_job(workspace)
    with Container(workspace=workspace) as container:
        repositories = container.repositories
        first = Worker(repositories)
        first.start()
        first.claim(identity)
        second = Worker(repositories)
        second.start()
        with pytest.raises(LeaseError):
            second.claim(identity)
        with pytest.raises(LeaseError):
            second.recover(identity)


def _detached_parent(workspace: str, connection: Any) -> None:
    _forbid_network()
    from imagen_cli.application.container import Container
    from imagen_cli.execution.worker import spawn_worker

    with Container(workspace=workspace) as container:
        row = spawn_worker(container.repositories, container.resolved_workspace.root,
                           heartbeat_seconds=0.1)
        connection.send({"pid": row["pid"], "id": row["id"],
                         "heartbeat_at": row["heartbeat_at"]})
    connection.close()
    # Parent process exits here; the detached worker must keep running.


def test_worker_survives_parent_exit_then_kill_is_not_alive(tmp_path: Path) -> None:
    if not worker_module.detached_supported():
        pytest.skip("detached worker is unsupported on this platform")
    # Windows has no SIGKILL; os.kill(SIGTERM) there is TerminateProcess, equally terminal.
    kill_signal = getattr(signal, "SIGKILL", signal.SIGTERM)
    workspace = str(tmp_path / "workspace")
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_detached_parent, args=(workspace, child))
    process.start()
    child.close()
    pid: int | None = None
    try:
        assert parent.poll(45), "detached worker handshake did not report"
        facts = parent.recv()
        process.join(10)
        assert process.exitcode == 0
        pid = facts["pid"]
        with Container(workspace=workspace) as container:
            repositories = container.repositories
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                row = repositories.get("workers", facts["id"])
                if row and row["heartbeat_at"] > facts["heartbeat_at"]:
                    break
                time.sleep(0.05)
            else:
                pytest.fail("detached worker did not heartbeat after parent exit")
            status, data = worker_module.status(repositories)
            assert status == "succeeded"
            assert data["workers"][0]["alive"] is True
        os.kill(pid, kill_signal)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and process_start(pid) is not None:
            time.sleep(0.05)
        with Container(workspace=workspace) as container:
            status, data = worker_module.status(container.repositories)
            assert data["workers"][0]["state"] == "running"
            assert data["workers"][0]["alive"] is False
            restarted = worker_module.spawn_worker(
                container.repositories, container.resolved_workspace.root,
                heartbeat_seconds=0.1,
            )
            assert restarted["id"] != facts["id"]
            assert restarted["state"] == "running"
            pid = restarted["pid"]
    finally:
        if process.is_alive():
            process.kill()
            process.join(5)
        if pid is not None:
            with suppress(ProcessLookupError, OSError):
                os.kill(pid, kill_signal)


def test_cli_worker_status_and_async_enqueue(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    prompt = seed(workspace, tmp_path)
    if not worker_module.detached_supported():
        pytest.skip("detached worker is unsupported on this platform")

    code, result = invoke(workspace, "generate", "--async", "--session", "poster",
                          "--prompt-file", str(prompt))
    assert code == 0, result
    assert result["status"] == "accepted"
    job = result["data"]["job"]
    assert job["state"] == "queued"
    assert job["dispatch_state"] == "not_started"
    assert job["executor_id"] is None
    assert job["worker_queued"] is True
    with Container(workspace=str(workspace)) as container:
        assert len(container.repositories.list("jobs")) == 1
        assert container.repositories.list("turns")[0]["creation_state"] == "queued"

    code, status = invoke(workspace, "worker", "status")
    assert code == 0
    assert status["data"]["workers"] == []
    with Container(workspace=str(workspace)) as container:
        assert len(container.repositories.list("jobs")) == 1


def test_async_gate_refuses_before_creating_anything(tmp_path: Path, monkeypatch) -> None:
    workspace = str(tmp_path / "workspace")
    monkeypatch.setattr(worker_module, "detached_supported", lambda: False)
    with Container(workspace=workspace, options=SharedOptions(async_=True)) as container:
        sessions = SessionService(container.repositories, container.artifact_store)
        session = sessions.create_session("gate")
        status, data = container.execute("generate", {
            "schema_version": 1, "operation": "generate", "session": session.id,
            "prompt": "never sent",
        })
        assert (status, data) == ("unsupported_capability", {})
        assert container.repositories.list("jobs") == []
        assert container.repositories.list("turns") == []
