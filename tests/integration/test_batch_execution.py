"""Bounded batch execution: ordering, limits, provider caps, no retries, event reads."""

import threading
import time
from pathlib import Path

import pytest

from imagen_cli.application.batch_service import BatchService, effective_concurrency
from imagen_cli.domain.contracts import CreationInput
from imagen_cli.storage.database import connect
from imagen_cli.storage.repositories import Repositories


@pytest.fixture
def repo(tmp_path: Path):
    connection = connect(tmp_path / "state.sqlite3")
    repository = Repositories(connection)
    repository.insert("sessions", {"id": "ses_batch", "name": "batch"})
    yield repository
    connection.close()


def request(prompt: str = "poster") -> CreationInput:
    return CreationInput(
        schema_version=1, operation="generate", session="ses_batch", prompt=prompt,
    )


def seed_jobs(repo: Repositories, count: int, **changes) -> list[str]:
    identities = []
    for index in range(count):
        turn = f"trn_{index:032x}"
        job = f"job_{index:032x}"
        repo.insert("turns", {"id": turn, "session_id": "ses_batch"})
        repo.insert("jobs", {"id": job, "turn_id": turn, **changes})
        identities.append(job)
    return identities


def concurrency_probe(identities, required, *, delay=0.01):
    barrier = threading.Barrier(required, timeout=5)
    lock = threading.Lock()
    state = {"active": 0, "maximum": 0}

    def run(item, index):
        with lock:
            state["active"] += 1
            state["maximum"] = max(state["maximum"], state["active"])
        try:
            barrier.wait()
            time.sleep(delay)
        finally:
            with lock:
                state["active"] -= 1
        return identities[index]

    return run, state


def test_child_job_order_follows_requests_even_when_completion_reorders(repo):
    identities = seed_jobs(repo, 2)
    finished = []

    def run(item, index):
        if index == 0:
            time.sleep(0.1)
        finished.append(index)
        return identities[index]

    service = BatchService(repo, prepare=lambda creation: creation, run=run)
    _, data = service.submit([request("first"), request("second")], 2)
    assert finished == [1, 0]
    assert data["batch"]["child_job_ids"] == identities
    assert [child["job_id"] for child in data["children"]] == identities


def test_request_limit_is_one_to_one_hundred_before_any_child_runs(repo):
    calls = []
    service = BatchService(
        repo, prepare=lambda creation: creation, run=lambda item, index: calls.append(index) or "",
    )
    for requests, concurrency in (
        ([], 2),
        ([request()] * 101, 2),
        ([request()], 0),
        ([request()], 9),
        ([request()], True),
    ):
        with pytest.raises(ValueError):
            service.submit(requests, concurrency)
    assert calls == []
    assert repo.list("batches") == []


def test_default_concurrency_is_two(repo):
    identities = seed_jobs(repo, 4)
    run, state = concurrency_probe(identities, 2)
    service = BatchService(repo, prepare=lambda creation: creation, run=run)
    _, data = service.submit([request(str(index)) for index in range(4)])
    assert data["batch"]["concurrency"] == 2
    assert state["maximum"] == 2
    assert data["batch"]["child_job_ids"] == identities


def test_maximum_configured_concurrency_is_eight(repo):
    identities = seed_jobs(repo, 8)
    run, state = concurrency_probe(identities, 4, delay=0.05)
    service = BatchService(repo, prepare=lambda creation: creation, run=run)
    _, data = service.submit([request(str(index)) for index in range(8)], 8)
    assert data["batch"]["concurrency"] == 8
    assert 4 <= state["maximum"] <= 8


def test_lower_provider_limit_wins_over_requested_concurrency(repo):
    identities = seed_jobs(repo, 4)
    run, state = concurrency_probe(identities, 2)
    service = BatchService(
        repo,
        prepare=lambda creation: creation,
        run=run,
        provider_limit=lambda item: 2,
    )
    _, data = service.submit([request(str(index)) for index in range(4)], 8)
    assert data["batch"]["concurrency"] == 2
    assert state["maximum"] <= 2


def test_mixed_providers_use_the_lowest_declared_limit(repo):
    identities = seed_jobs(repo, 4)
    run, state = concurrency_probe(identities, 2)
    seen = []

    def prepare(creation):
        provider = "openai" if creation.prompt.endswith(("0", "2")) else "gemini"
        seen.append(provider)
        return {"provider": provider, "creation": creation}

    service = BatchService(
        repo,
        prepare=prepare,
        run=run,
        provider_limit=lambda item: {"openai": 2, "gemini": 4}[item["provider"]],
    )
    _, data = service.submit([request(str(index)) for index in range(4)], 8)
    assert set(seen) == {"openai", "gemini"}
    assert data["batch"]["concurrency"] == 2
    assert state["maximum"] <= 2
    assert [child["job_id"] for child in data["children"]] == identities


def test_effective_concurrency_never_exceeds_eight_or_provider_cap():
    assert effective_concurrency(None, []) == 2
    assert effective_concurrency(8, [None, 5]) == 5
    assert effective_concurrency(8, [None, 20]) == 8
    with pytest.raises(ValueError):
        effective_concurrency(1, [0])


def test_child_failure_is_not_retried_and_creates_no_batch(repo):
    calls = []

    def run(item, index):
        calls.append(index)
        raise RuntimeError("provider refused")

    service = BatchService(repo, prepare=lambda creation: creation, run=run)
    with pytest.raises(RuntimeError, match="provider refused"):
        service.submit([request()], 2)
    assert calls == [0]
    assert repo.list("batches") == []


def test_child_events_aggregate_in_child_order_without_rewriting_jobs(repo):
    identities = seed_jobs(repo, 2, state="failed", remote_state="failed")
    repo.append_event(identities[0], "job_updated", {"phase": "one"})
    repo.append_event(identities[0], "artifact_saved", {"phase": "two"})
    repo.append_event(identities[1], "job_updated", {"phase": "three"})
    service = BatchService(
        repo, prepare=lambda creation: creation, run=lambda item, index: identities[index],
    )
    status, submitted = service.submit([request("a"), request("b")], 2)
    assert status == "failed"
    before = repo.list("jobs")
    status, data = service.get(submitted["batch"]["id"])
    assert status == "succeeded"
    assert [(event["job_id"], event["job_seq"], event["event_type"])
            for event in data["events"]] == [
        (identities[0], 1, "job_updated"),
        (identities[0], 2, "artifact_saved"),
        (identities[1], 1, "job_updated"),
    ]
    assert repo.list("jobs") == before
    assert data["summary"]["state"] == "failed"
    assert data["summary"]["complete_success_count"] == 0


def test_wait_reports_timeout_while_a_child_is_still_active(repo):
    identities = seed_jobs(repo, 1, state="running", remote_state="running")
    service = BatchService(
        repo, prepare=lambda creation: creation, run=lambda item, index: identities[index],
    )
    _, submitted = service.submit([request()], 1)
    status, data = service.wait(submitted["batch"]["id"], timeout=1)
    assert status == "wait_timeout"
    assert data["summary"]["state"] == "running"
    assert repo.get("jobs", identities[0])["state"] == "running"


def test_cli_batch_submit_rejects_before_any_child_and_get_is_read_only(tmp_path):
    import io
    import json

    from imagen_cli.application.container import Container
    from imagen_cli.cli.entrypoint import main

    workspace = tmp_path / "ws"
    source = tmp_path / "requests.json"
    source.write_text(json.dumps({"requests": [{
        "schema_version": 1, "operation": "generate",
        "session": "missing", "prompt": "海报",
    }]}), encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    code = main([
        "--workspace", str(workspace), "--output", "json", "batch", "submit",
        "--input-json", str(source),
    ], stdout=out, stderr=err)
    assert code == 2
    assert json.loads(out.getvalue())["status"] == "validation_error"
    with Container(workspace=str(workspace)) as container:
        assert container.repositories.list("batches") == []
        assert container.repositories.list("jobs") == []
    out, err = io.StringIO(), io.StringIO()
    code = main([
        "--workspace", str(workspace), "--output", "json", "batch", "get",
        "bat_" + "0" * 32,
    ], stdout=out, stderr=err)
    assert code == 4
    assert json.loads(out.getvalue())["status"] == "not_found"

