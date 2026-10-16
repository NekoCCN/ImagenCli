"""Offline performance harness: real SQLite schema/repositories, no network.

Defaults follow plan.md: 10,000 turns, 20,000 artifacts, 10 concurrent readers,
100 metadata samples, 20 cold-start runs for help/schema. Prints one JSON report.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from imagen_cli.application.session_service import SessionService  # noqa: E402
from imagen_cli.storage.artifacts import ArtifactStore  # noqa: E402
from imagen_cli.storage.database import connect  # noqa: E402
from imagen_cli.storage.repositories import Repositories  # noqa: E402
from imagen_cli.storage.workspace import resolve_workspace  # noqa: E402

_ENTRY = (
    "import socket,sys; "
    "socket.socket=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network')); "
    "socket.getaddrinfo=socket.socket; "
    "from imagen_cli.cli.entrypoint import main; sys.exit(main())"
)
_TS = "2026-10-24T00:00:00Z"
_METADATA_TARGET_MS = 500
_COLD_START_TARGET_MS = 2000


def _typed(prefix: str, index: int) -> str:
    """Deterministic UUIDv4-shaped identifier accepted by the domain types."""
    return f"{prefix}_{index:012x}4{0:03x}8{index:015x}"


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def _turn(index: int, session_id: str) -> dict[str, Any]:
    return {
        "id": _typed("trn", index),
        "session_id": session_id,
        "parent_turn_id": None,
        "instruction": "benchmark",
        "operation": "generate",
        "resolved_profile_snapshot": {},
        "model": "bench-model",
        "parameters": {},
        "input_manifest": [],
        "expected_session_version": 0,
        # Bound after both sides of the circular turn/job FK exist.
        "job_id": None,
        "creation_state": "succeeded",
        "publication_state": "not_attempted",
        "created_at": _TS,
        "finished_at": None,
    }


def seed(workspace: Path, turns: int, artifacts: int, sessions: int) -> dict[str, Any]:
    """Seed through the real migration and repository validation."""
    if sessions < 1 or turns < sessions or artifacts < 0:
        raise ValueError("need at least one session and one turn per session")
    resolved = resolve_workspace(workspace)
    connection = connect(resolved.database)
    repositories = Repositories(connection)
    started = time.perf_counter()
    session_ids = [_typed("ses", index) for index in range(sessions)]
    job_ids: list[str] = []
    try:
        with repositories.transaction():
            for index, identity in enumerate(session_ids):
                repositories.insert("sessions", {
                    "id": identity,
                    "name": f"bench-{index:06d}",
                    "intent": "",
                    "version": 0,
                    "created_at": _TS,
                })
            for index in range(turns):
                session_id = session_ids[index % sessions]
                repositories.insert("turns", _turn(index, session_id))
                job_ids.append(_typed("job", index))
                repositories.insert("jobs", {
                    "id": job_ids[-1],
                    "turn_id": _typed("trn", index),
                    "state": "succeeded",
                    "dispatch_state": "not_started",
                    "remote_state": "succeeded",
                    "local_save_state": "saved",
                    "requested_count": 1,
                    "actual_count": 1,
                    "accepted_maybe": False,
                    "created_at": _TS,
                    "updated_at": _TS,
                })
            connection.execute(
                "UPDATE turns SET job_id='job_' || substr(id, 5) WHERE job_id IS NULL"
            )
            for index in range(artifacts):
                turn_index = index % turns
                turn_id = _typed("trn", turn_index)
                session_id = session_ids[turn_index % sessions]
                identity = _typed("art", index)
                repositories.insert("artifacts", {
                    "id": identity,
                    "managed_relpath": f"bench/{index:06d}.png",
                    "save_state": "published",
                    "media_type": "image/png",
                    "width": 1,
                    "height": 1,
                    "byte_size": 1,
                    "sha256": "0" * 64,
                    "origin_manifest": {"kind": "benchmark"},
                    "created_at": _TS,
                })
                repositories.insert("artifact_references", {
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "artifact_id": identity,
                    "role": "output",
                    "index": index // turns,
                })
    finally:
        connection.close()
    return {
        "turns": turns,
        "artifacts": artifacts,
        "sessions": sessions,
        "jobs": len(job_ids),
        "seed_seconds": round(time.perf_counter() - started, 3),
    }


def _query_worker(
    database: Path,
    artifact_root: Path,
    session_ids: list[str],
    job_ids: list[str],
    iterations: int,
    start: int,
    results: list[tuple[str, float]],
    errors: list[BaseException],
    lock: threading.Lock,
) -> None:
    connection = None
    local: list[tuple[str, float]] = []
    try:
        connection = connect(database)
        repositories = Repositories(connection)
        service = SessionService(repositories, ArtifactStore(artifact_root, repositories))
        for offset in range(iterations):
            position = start + offset
            for kind in ("job_get", "session_show", "session_list"):
                begun = time.perf_counter()
                if kind == "job_get":
                    repositories.get("jobs", job_ids[position % len(job_ids)])
                elif kind == "session_show":
                    service.resume(session_ids[position % len(session_ids)])
                else:
                    repositories.list(
                        "sessions", limit=100, offset=(position * 7) % len(session_ids),
                    )
                local.append((kind, (time.perf_counter() - begun) * 1000))
    except BaseException as error:
        with lock:
            errors.append(error)
    finally:
        if connection is not None:
            connection.close()
    with lock:
        results.extend(local)


def measure_metadata(
    workspace: Path, sessions: int, turns: int, concurrency: int, samples: int
) -> dict[str, Any]:
    resolved = resolve_workspace(workspace)
    session_ids = [_typed("ses", index) for index in range(sessions)]
    job_ids = [_typed("job", index) for index in range(turns)]
    results: list[tuple[str, float]] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    base, extra = divmod(samples, concurrency)
    threads = [
        threading.Thread(
            target=_query_worker,
            args=(
                resolved.database, resolved.artifacts, session_ids, job_ids,
                base + (1 if index < extra else 0),
                index * base + min(index, extra), results, errors, lock,
            ),
        )
        for index in range(concurrency)
    ]
    started = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if errors:
        raise RuntimeError(f"metadata query failed: {errors[0]!r}") from errors[0]
    wall_ms = round((time.perf_counter() - started) * 1000, 3)
    by_kind: dict[str, list[float]] = {}
    for kind, value in results:
        by_kind.setdefault(kind, []).append(value)
    return {
        "samples": len(results),
        "samples_per_kind": samples,
        "concurrency": concurrency,
        "wall_ms": wall_ms,
        "p95_ms": round(_p95([value for _, value in results]), 3),
        "average_ms": round(
            sum(value for _, value in results) / len(results), 3,
        ) if results else None,
        "by_kind": {
            kind: {"samples": len(values), "p95_ms": round(_p95(values), 3)}
            for kind, values in by_kind.items()
        },
    }


def measure_cold_start(
    workspace: Path, command: list[str], runs: int
) -> tuple[dict[str, Any], list[float]]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["NO_COLOR"] = "1"
    samples: list[float] = []
    for _ in range(runs):
        begun = time.perf_counter()
        completed = subprocess.run(
            [sys.executable, "-c", _ENTRY, "--workspace", str(workspace), *command],
            input=b"", env=env, capture_output=True, timeout=60, check=False,
        )
        elapsed = (time.perf_counter() - begun) * 1000
        if completed.returncode != 0:
            raise RuntimeError(
                f"cold-start {command} failed: {completed.stderr.decode(errors='replace')}"
            )
        samples.append(elapsed)
    return {"runs": runs, "p95_ms": round(_p95(samples), 3)}, samples


def run(turns: int, artifacts: int, sessions: int, concurrency: int,
        samples: int, cold_runs: int, workspace: Path) -> dict[str, Any]:
    workspace.mkdir(parents=True, exist_ok=True)
    dataset = seed(workspace, turns, artifacts, sessions)
    metadata = measure_metadata(workspace, sessions, turns, concurrency, samples)
    help_start, help_samples = measure_cold_start(
        workspace, ["help", "--output", "json"], cold_runs,
    )
    schema_start, schema_samples = measure_cold_start(
        workspace, ["schema", "--output", "json"], cold_runs,
    )
    combined = _p95(help_samples + schema_samples)
    return {
        "schema_version": 1,
        "benchmark": "offline-local-metadata",
        "network": False,
        "environment": {
            "os": platform.platform(),
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "cpu_count": os.cpu_count(),
        },
        "dataset": dataset,
        "metadata": metadata,
        "cold_start": {
            "help": help_start,
            "schema": schema_start,
            "combined_p95_ms": round(combined, 3),
        },
        "targets_ms": {
            "metadata_p95": _METADATA_TARGET_MS,
            "cold_start_p95": _COLD_START_TARGET_MS,
        },
        "within_target": {
            "metadata_p95": metadata["p95_ms"] <= _METADATA_TARGET_MS,
            "cold_start_p95": combined <= _COLD_START_TARGET_MS,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--turns", type=int, default=10_000)
    parser.add_argument("--artifacts", type=int, default=20_000)
    parser.add_argument("--sessions", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--samples", type=int, default=100,
                        help="positions per query kind (job_get/session_show/session_list)")
    parser.add_argument("--cold-runs", type=int, default=20)
    parser.add_argument("--workspace")
    args = parser.parse_args(argv)
    if args.sessions < 1 or args.turns < args.sessions:
        parser.error("--turns must be at least --sessions, which must be positive")
    if args.artifacts < 0 or args.concurrency < 1 or args.samples < 1 or args.cold_runs < 1:
        parser.error("counts must be positive")
    temporary = None
    if args.workspace is None:
        temporary = tempfile.mkdtemp(prefix="imagen-bench-")
        workspace = Path(temporary)
    else:
        workspace = Path(args.workspace)
    report = run(
        args.turns, args.artifacts, args.sessions, args.concurrency,
        args.samples, args.cold_runs, workspace,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if temporary is not None:
        import shutil

        shutil.rmtree(temporary, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
