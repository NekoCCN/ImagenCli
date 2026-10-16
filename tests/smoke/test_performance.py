"""Performance harness smoke check at tiny scale; excluded from default CI runs."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.performance
def test_benchmark_reports_shape_and_loose_bounds(tmp_path: Path) -> None:
    env = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(ROOT / "src"),
        "HOME": str(tmp_path / "home"),
        "NO_COLOR": "1",
    }
    proc = subprocess.run(
        [
            sys.executable, str(ROOT / "scripts" / "bench.py"),
            "--turns", "20", "--artifacts", "40", "--sessions", "4",
            "--concurrency", "2", "--samples", "10", "--cold-runs", "1",
            "--workspace", str(tmp_path / "bench"),
        ],
        input=b"", env=env, capture_output=True, timeout=120, check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode()
    assert proc.stderr == b""
    report = json.loads(proc.stdout)
    assert report["schema_version"] == 1
    assert report["network"] is False
    assert report["dataset"]["turns"] == 20
    assert report["dataset"]["artifacts"] == 40
    assert report["dataset"]["sessions"] == 4
    assert report["metadata"]["samples_per_kind"] == 10
    assert report["metadata"]["samples"] == 30
    assert report["metadata"]["concurrency"] == 2
    assert set(report["metadata"]["by_kind"]) == {
        "job_get", "session_show", "session_list",
    }
    assert report["cold_start"]["help"]["runs"] == 1
    assert report["cold_start"]["schema"]["runs"] == 1
    # Loose bounds only catch a broken harness; plan.md's real targets are stricter.
    assert report["metadata"]["p95_ms"] < 5000
    assert report["cold_start"]["combined_p95_ms"] < 30_000
    assert (tmp_path / "bench" / "state.sqlite3").is_file()
