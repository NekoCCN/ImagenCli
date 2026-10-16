"""Lease/fencing worker lifecycle; recovery never re-sends a create.

Leases and tokens fence local database writes only. An expired lease may be
taken over for observation, never to dispatch again: once a job has a lease
token, `claim` refuses it forever and only `recover` may observe it.
"""

import argparse
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from imagen_cli.application.cancel_service import process_start
from imagen_cli.domain.primitive import new_id
from imagen_cli.storage.repositories import Repositories

HEARTBEAT_SECONDS = 5.0
LEASE_SECONDS = 30.0
HANDSHAKE_SECONDS = 10.0
_TERMINAL = {"succeeded", "failed", "canceled"}


class LeaseError(ValueError):
    """The caller no longer holds a valid worker identity or lease token."""


def detached_supported() -> bool:
    """Detached startup is only claimed where process identity can be verified."""
    return process_start(os.getpid()) is not None


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _expired(value: str | None, now: datetime) -> bool:
    if not value:
        return False
    expires = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return expires.tzinfo is not None and expires <= now


def _alive(row: dict[str, Any] | None) -> bool:
    if not row or row.get("state") != "running":
        return False
    pid = row.get("pid")
    return type(pid) is int and process_start(pid) == row.get("boot_identity")


class Worker:
    def __init__(
        self,
        repositories: Repositories,
        *,
        worker_id: str | None = None,
        start_nonce: str | None = None,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
        lease_seconds: float = LEASE_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if heartbeat_seconds <= 0 or lease_seconds <= 0:
            raise ValueError("heartbeat and lease must be positive")
        self.repositories = repositories
        self.worker_id = worker_id or new_id("wrk")
        self.start_nonce = start_nonce or uuid4().hex
        self.heartbeat_seconds = heartbeat_seconds
        self.lease_seconds = lease_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._pid = os.getpid()
        self._boot_identity = process_start(self._pid)
        self._tokens: dict[str, str] = {}

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("clock must be timezone-aware")
        return value.astimezone(UTC)

    def _identity(self) -> dict[str, Any]:
        return {
            "pid": self._pid,
            "start": self._boot_identity,
            "worker_id": self.worker_id,
            "start_nonce": self.start_nonce,
        }

    def _verified(self, *, states: set[str]) -> dict[str, Any]:
        row = self.repositories.get("workers", self.worker_id)
        if (
            row is None
            or row.get("pid") != self._pid
            or row.get("boot_identity") != self._boot_identity
            or row.get("start_nonce") != self.start_nonce
            or row.get("state") not in states
        ):
            raise LeaseError("worker identity is not the registered executor")
        return cast(dict[str, Any], row)

    def start(self) -> dict[str, Any]:
        """Persist pid, boot identity and nonce, then complete the handshake."""
        if self._boot_identity is None:
            raise LeaseError("process boot identity is unavailable")
        now = self._now()
        row = {
            "id": self.worker_id,
            "pid": self._pid,
            "boot_identity": self._boot_identity,
            "state": "starting",
            "heartbeat_at": _timestamp(now),
            "start_nonce": self.start_nonce,
        }
        try:
            self.repositories.insert("workers", row)
        except sqlite3.IntegrityError as error:
            raise LeaseError("worker identity already registered") from error
        return self.heartbeat()

    def heartbeat(self) -> dict[str, Any]:
        """Refresh liveness and only the leases this worker still owns."""
        now = self._now()
        with self.repositories.transaction():
            self._verified(states={"starting", "running"})
            row = self.repositories.update(
                "workers", self.worker_id,
                {"state": "running", "heartbeat_at": _timestamp(now)},
            )
            for job_id, token in list(self._tokens.items()):
                job = self.repositories.get("jobs", job_id, include_private=True)
                if job is None or job.get("lease_token") != token or _expired(
                    job.get("lease_expires_at"), now
                ):
                    self._tokens.pop(job_id, None)
                    continue
                self.repositories.update(
                    "jobs", job_id,
                    {"lease_expires_at": _timestamp(now + timedelta(seconds=self.lease_seconds))},
                )
        return cast(dict[str, Any], row)

    def claim(self, job_id: str) -> str:
        """Take the one dispatch lease a job can ever have."""
        now = self._now()
        with self.repositories.transaction():
            self._verified(states={"running"})
            job = self.repositories.get("jobs", job_id, include_private=True)
            if job is None:
                raise KeyError(job_id)
            if job.get("lease_token") is not None:
                raise LeaseError("job was already leased; only recover-only takeover is allowed")
            if job.get("state") != "queued" or job.get("dispatch_state") != "not_started":
                raise LeaseError("job is not a queued, undispatched request")
            token = uuid4().hex
            self.repositories.update(
                "jobs", job_id,
                {
                    "executor_id": self._identity(),
                    "lease_token": token,
                    "lease_expires_at": _timestamp(
                        now + timedelta(seconds=self.lease_seconds)
                    ),
                    "updated_at": _timestamp(now),
                },
            )
            self.repositories.append_event(
                job_id, "lease_claimed",
                {"worker_id": self.worker_id, "mode": "dispatch"},
            )
            self._tokens[job_id] = token
            return token

    def owns(self, job_id: str) -> bool:
        job = self.repositories.get("jobs", job_id, include_private=True)
        token = self._tokens.get(job_id)
        return (
            job is not None
            and token is not None
            and job.get("lease_token") == token
            and not _expired(job.get("lease_expires_at"), self._now())
        )

    def fenced_update(
        self, job_id: str, changes: dict[str, Any], *, event: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Apply a result only if this worker still owns the fencing token."""
        with self.repositories.transaction():
            if not self.owns(job_id):
                raise LeaseError("stale lease token; keep the result for reconcile")
            row = self.repositories.update("jobs", job_id, changes)
            if event is not None:
                self.repositories.append_event(job_id, event, payload or {})
            return cast(dict[str, Any], row)

    def stop(self) -> dict[str, Any]:
        with self.repositories.transaction():
            self._verified(states={"starting", "running", "stopping"})
            row = self.repositories.update(
                "workers", self.worker_id,
                {"state": "stopped", "heartbeat_at": _timestamp(self._now())},
            )
            self._tokens.clear()
            return cast(dict[str, Any], row)

    def recover(self, job_id: str | None = None) -> list[dict[str, Any]]:
        """Take over expired leases for observation without dispatching."""
        now = self._now()
        with self.repositories.transaction():
            self._verified(states={"running"})
            jobs = (
                [self.repositories.get("jobs", job_id, include_private=True)]
                if job_id is not None
                else self._recoverable()
            )
            recovered: list[dict[str, Any]] = []
            for job in jobs:
                if job is None:
                    raise KeyError(job_id)
                if job["state"] in _TERMINAL:
                    continue
                if (
                    job["dispatch_state"] == "not_started"
                    and job.get("lease_token") is None
                ):
                    # Nothing was ever started, so there is nothing to observe.
                    continue
                if job["state"] != "unknown" and not _expired(
                    job.get("lease_expires_at"), now
                ):
                    if job_id is None:
                        continue
                    raise LeaseError("active lease cannot be taken over")
                token = uuid4().hex
                self.repositories.update(
                    "jobs", job["id"],
                    {
                        "executor_id": self._identity(),
                        "lease_token": token,
                        "lease_expires_at": _timestamp(
                            now + timedelta(seconds=self.lease_seconds)
                        ),
                        "updated_at": _timestamp(now),
                    },
                )
                self.repositories.append_event(
                    job["id"], "recover_only_takeover",
                    {
                        "worker_id": self.worker_id,
                        "mode": "recover_only",
                        "dispatch_state": job["dispatch_state"],
                    },
                )
                self._tokens[job["id"]] = token
                recovered.append({
                    "job": self.repositories.get("jobs", job["id"]),
                    "mode": "recover_only",
                    "creates_paid_request": False,
                })
            return recovered

    def _recoverable(self) -> Iterator[dict[str, Any]]:
        offset = 0
        while True:
            page = self.repositories.list("jobs", limit=1000, offset=offset, include_private=True)
            for job in page:
                if job["state"] in _TERMINAL:
                    continue
                if job["dispatch_state"] != "not_started" or job.get("lease_token") is not None:
                    yield job
            if len(page) < 1000:
                return
            offset += len(page)


def status(repositories: Repositories) -> tuple[str, dict[str, Any]]:
    rows = repositories.list("workers", limit=1000)
    for row in rows:
        row["alive"] = _alive(row)
    return "succeeded", {"workers": rows}


def spawn_worker(
    repositories: Repositories,
    workspace: str | Path,
    *,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
    lease_seconds: float = LEASE_SECONDS,
    timeout: float = HANDSHAKE_SECONDS,
) -> dict[str, Any]:
    """Start one detached worker and wait for its persisted handshake."""
    if not detached_supported():
        raise LeaseError("detached worker startup is unsupported on this platform")
    for row in repositories.list("workers", limit=1000):
        if _alive(row):
            return row
    worker_id = new_id("wrk")
    source_root = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(source_root), *([environment["PYTHONPATH"]] if environment.get("PYTHONPATH") else [])]
    )
    process = subprocess.Popen(
        [
            sys.executable, "-m", "imagen_cli.execution.worker",
            "--workspace", str(workspace),
            "--worker-id", worker_id,
            "--heartbeat", str(heartbeat_seconds),
            "--lease", str(lease_seconds),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=os.name != "nt",
        creationflags=(
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        ) if os.name == "nt" else 0,
        close_fds=True,
        env=environment,
    )
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise LeaseError("detached worker exited before its handshake")
            row = repositories.get("workers", worker_id)
            if row is not None and row.get("state") == "running" and row.get("pid") == process.pid:
                return cast(dict[str, Any], row)
            time.sleep(0.05)
        raise LeaseError("detached worker handshake timed out")
    except BaseException:
        if process.poll() is None:
            with suppress(OSError):
                process.terminate()
        raise


def _serve(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--heartbeat", type=float, default=HEARTBEAT_SECONDS)
    parser.add_argument("--lease", type=float, default=LEASE_SECONDS)
    arguments = parser.parse_args(argv)

    from imagen_cli.application.container import Container

    stopping = threading.Event()

    def stop(_signum: int, _frame: Any) -> None:
        stopping.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with Container(workspace=arguments.workspace) as container:
        worker = Worker(
            container.repositories,
            worker_id=arguments.worker_id,
            heartbeat_seconds=arguments.heartbeat,
            lease_seconds=arguments.lease,
        )
        worker.start()
        while not stopping.wait(worker.heartbeat_seconds):
            try:
                worker.heartbeat()
            except LeaseError:
                break
        with suppress(LeaseError):
            worker.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(_serve(sys.argv[1:]))
