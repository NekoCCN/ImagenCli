"""Remote endpoints: registration, explicit digest consent, submit and artifact."""

import base64
import io

import httpx
import pytest
from PIL import Image

from imagen_cli.application import remote_service, watermark_service
from imagen_cli.application.container import Container
from imagen_cli.application.session_service import SessionService
from imagen_cli.cli.entrypoint import _parse

JOB = "job_0123456789ab4def8abc0123456789ab"


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), (5, 6, 7)).save(buffer, format="PNG")
    return buffer.getvalue()


class Server:
    def __init__(self, mode="single-replica-sync"):
        self.mode = mode
        self.calls = []
        self.fail_posts = False
        self.job_state = "succeeded"
        self.bodies: list[bytes] = []

    def __call__(self, request):
        self.calls.append((request.method, request.url.path))
        path = request.url.path
        if path == "/v1/meta":
            return httpx.Response(200, json={
                "schema_version": 1, "state_mode": self.mode, "gpu": "L4",
                "worker_digest": "sha256:abc", "raiw_version": "0.53.0",
                "imagen_cli_version": "0.1.17", "max_attempts": 1,
                "sync_max_seconds": 90,
            })
        if request.method == "POST" and path == "/v1/jobs":
            self.bodies.append(request.content)
            if self.fail_posts:
                return httpx.Response(503, json={"status": "unknown"})
            if self.mode == "single-replica-sync":
                return httpx.Response(200, json={
                    "job_id": JOB, "status": "cleaned", "report": {"status": "cleaned"},
                    "output": {"sha256": "c" * 64, "media_type": "image/png",
                               "bytes": len(_png()),
                               "base64": base64.b64encode(_png()).decode()},
                })
            return httpx.Response(202, json={"job_id": JOB, "status": "queued"})
        if path == f"/v1/jobs/{JOB}" and request.method == "DELETE":
            return httpx.Response(200, json={"job_id": JOB, "status": "canceled"})
        if path == f"/v1/jobs/{JOB}":
            return httpx.Response(200, json={
                "job_id": JOB, "status": self.job_state, "report": {"status": "cleaned"},
            })
        if path == f"/v1/jobs/{JOB}/output":
            return httpx.Response(200, content=_png(), headers={"content-type": "image/png"})
        return httpx.Response(404, json={"status": "unknown", "reason": "state_not_found"})


@pytest.fixture
def remote(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEN_TEST_TOKEN", "super-secret")
    monkeypatch.setattr(remote_service.i18n, "config_path", lambda: tmp_path / "config.json")
    server = Server()
    monkeypatch.setattr(remote_service, "TRANSPORT", httpx.MockTransport(server))
    return server, tmp_path


@pytest.fixture
def published(tmp_path):
    with Container(workspace=str(tmp_path / "workspace")) as container:
        service = SessionService(container.repositories, container.artifact_store)
        session = service.create_session("remote")
        turn = service.create_turn(
            session.id, instruction="source", operation="generate",
            resolved_profile_snapshot={}, model="fixture", parameters={},
        )
        source = service.save_artifact(
            turn.id, _png(), origin_manifest={"kind": "fixture"},
        )
        yield container, source


def test_add_probes_meta_and_never_stores_the_secret(remote):
    server, tmp_path = remote
    record = remote_service.add("modal", "https://worker.example", "env:IMAGEN_TEST_TOKEN")
    assert record["state_mode"] == "single-replica-sync"
    assert record["gpu"] == "L4"
    assert ("GET", "/v1/meta") in server.calls
    text = (tmp_path / "config.json").read_text()
    assert "super-secret" not in text
    assert "env:IMAGEN_TEST_TOKEN" in text


def test_plain_http_is_rejected_off_localhost(remote):
    with pytest.raises(ValueError, match="https"):
        remote_service.add("modal", "http://worker.example", "env:IMAGEN_TEST_TOKEN")


def test_a_missing_credential_is_reported(remote, monkeypatch):
    monkeypatch.delenv("IMAGEN_TEST_TOKEN")
    with pytest.raises(ValueError, match="not set"):
        remote_service.add("modal", "https://worker.example", "env:IMAGEN_TEST_TOKEN")


def test_submit_without_the_digest_makes_no_network_call(remote, published):
    server, _ = remote
    container, source = published
    remote_service.add("modal", "https://worker.example", "env:IMAGEN_TEST_TOKEN")
    server.calls.clear()
    status, data = remote_service.run_operation(
        container.repositories, container.artifact_store, source.id, "modal",
        "watermark.visible",
    )
    assert status == "approval_required"
    assert server.calls == []
    assert len(data["job_digest"]) == 64


def test_the_digest_changes_with_the_request(remote, published):
    _, _ = remote
    container, source = published
    remote_service.add("modal", "https://worker.example", "env:IMAGEN_TEST_TOKEN")
    first = remote_service.run_operation(
        container.repositories, container.artifact_store, source.id, "modal",
        "watermark.visible", params={"force": True},
    )[1]["job_digest"]
    second = remote_service.run_operation(
        container.repositories, container.artifact_store, source.id, "modal",
        "watermark.visible", params={"force": False},
    )[1]["job_digest"]
    assert first != second


def test_sync_submit_publishes_a_new_artifact(remote, published):
    server, _ = remote
    container, source = published
    remote_service.add("modal", "https://worker.example", "env:IMAGEN_TEST_TOKEN")
    approved = remote_service.run_operation(
        container.repositories, container.artifact_store, source.id, "modal",
        "watermark.visible",
    )[1]["job_digest"]
    before = container.artifact_store.get(source.id).read_bytes()
    status, data = remote_service.run_operation(
        container.repositories, container.artifact_store, source.id, "modal",
        "watermark.visible", accept_remote=approved,
    )
    assert status == "succeeded"
    assert data["artifact"]["id"] != source.id
    assert container.artifact_store.get(source.id).read_bytes() == before
    engine = data["artifact"]["origin_manifest"]["engine"]
    assert engine["provider"] == "modal"
    assert engine["gpu"] == "L4"
    assert any(path == "/v1/jobs" for method, path in server.calls if method == "POST")


def test_async_submit_polls_and_fetches_the_output(remote, published):
    server, _ = remote
    container, source = published
    server.mode = "queue"
    remote_service.add("modal", "https://worker.example", "env:IMAGEN_TEST_TOKEN")
    approved = remote_service.run_operation(
        container.repositories, container.artifact_store, source.id, "modal",
        "watermark.visible", max_seconds=5,
    )[1]["job_digest"]
    status, data = remote_service.run_operation(
        container.repositories, container.artifact_store, source.id, "modal",
        "watermark.visible", accept_remote=approved, max_seconds=5,
    )
    assert status == "succeeded"
    assert data["artifact"]["origin_manifest"]["remote_status"] == "succeeded"
    assert ("GET", f"/v1/jobs/{JOB}/output") in server.calls


def test_remote_commands_parse():
    assert _parse([
        "remote", "add", "modal", "--endpoint", "https://x.example",
        "--credential-ref", "env:TOKEN",
    ])[1] == {"name": "modal", "endpoint": "https://x.example",
              "credential_ref": "env:TOKEN"}
    command, raw, _ = _parse([
        "watermark", "invisible", "art_0123456789ab4def8abc0123456789ab",
        "--remote", "modal", "--accept-remote", "a" * 64, "--max-seconds", "600",
    ])
    assert command == "watermark invisible"
    assert isinstance(raw, dict)
    args = raw
    assert args["remote"] == "modal"
    assert args["max_seconds"] == 600


def _approved_submit(remote, published):
    container, source = published
    remote_service.add("modal", "https://worker.example", "env:IMAGEN_TEST_TOKEN")
    approved = remote_service.run_operation(
        container.repositories, container.artifact_store, source.id, "modal",
        "watermark.visible",
    )[1]["job_digest"]
    return remote_service.run_operation(
        container.repositories, container.artifact_store, source.id, "modal",
        "watermark.visible", accept_remote=approved,
    )[1]


def test_remote_jobs_are_recorded_with_their_artifact(remote, published):
    container, source = published
    data = _approved_submit(remote, published)
    rows = container.repositories.list("remote_jobs")
    assert len(rows) == 1
    row = rows[0]
    assert row["provider"] == "modal"
    assert row["status"] == "succeeded"
    assert row["artifact_id"] == data["artifact"]["id"]
    assert row["input_artifact_id"] == source.id
    assert len(row["job_digest"]) == 64


def test_job_get_and_resume_read_the_record(remote, published):
    container, _ = published
    data = _approved_submit(remote, published)
    job_id = data["remote_job"]
    got = remote_service.job_get(container.repositories, job_id)
    assert got["job"]["id"] == job_id
    assert got["live"]["status"] == "succeeded"
    status, resumed = remote_service.job_resume(
        container.repositories, container.artifact_store, job_id,
    )
    assert status == "succeeded"
    assert resumed["job"]["artifact_id"] == data["artifact"]["id"]


def test_retry_interrupted_reuses_the_same_digest(remote, published):
    server, _ = remote
    container, _ = published
    data = _approved_submit(remote, published)
    job_id = data["remote_job"]
    container.repositories.update("remote_jobs", job_id, {"status": "interrupted"})
    status, retried = remote_service.job_retry_interrupted(
        container.repositories, container.artifact_store, job_id,
    )
    assert status == "accepted"
    assert retried["job"]["job_digest"] == data["job_digest"]
    assert [call for call in server.calls if call == ("POST", "/v1/jobs")]


def test_retry_refuses_a_finished_job(remote, published):
    container, _ = published
    data = _approved_submit(remote, published)
    with pytest.raises(ValueError, match="interrupted"):
        remote_service.job_retry_interrupted(
            container.repositories, container.artifact_store, data["remote_job"],
        )


def test_cancel_records_the_worker_answer(remote, published):
    container, _ = published
    data = _approved_submit(remote, published)
    status, canceled = remote_service.job_cancel(
        container.repositories, data["remote_job"],
    )
    assert status in {"succeeded", "unknown", "canceled"}
    assert canceled["job"]["status"] in {"succeeded", "unknown", "canceled"}


def test_remote_job_commands_parse():
    assert _parse(["remote", "jobs", "list"])[0] == "remote jobs list"
    command, args, _ = _parse(["remote", "job", "get", "rmt_" + "0" * 32])
    assert command == "remote job get"
    assert args == {"job": "rmt_" + "0" * 32}


def test_the_same_approved_request_is_not_submitted_twice(remote, published):
    server, _ = remote
    container, _ = published
    first = _approved_submit(remote, published)
    posts = [call for call in server.calls if call == ("POST", "/v1/jobs")]
    again = remote_service.run_operation(
        container.repositories, container.artifact_store, published[1].id, "modal",
        "watermark.visible", accept_remote=first["job_digest"],
    )[1]
    assert again["replayed"] is True
    assert again["remote_job"] == first["remote_job"]
    assert [call for call in server.calls if call == ("POST", "/v1/jobs")] == posts
    assert len(container.repositories.list("remote_jobs")) == 1


def test_the_worker_receives_the_request_digest_not_the_file_hash(remote, published):
    server, _ = remote
    container, _ = published
    remote_service.add("modal", "https://worker.example", "env:IMAGEN_TEST_TOKEN")
    approved = remote_service.run_operation(
        container.repositories, container.artifact_store, published[1].id, "modal",
        "watermark.visible",
    )[1]["job_digest"]
    remote_service.run_operation(
        container.repositories, container.artifact_store, published[1].id, "modal",
        "watermark.visible", accept_remote=approved,
    )
    assert approved.encode() in server.bodies[-1]


def test_a_queued_row_without_a_job_id_is_submitted_again(remote, published):
    server, _ = remote
    container, _ = published
    remote_service.add("modal", "https://worker.example", "env:IMAGEN_TEST_TOKEN")
    approved = remote_service.run_operation(
        container.repositories, container.artifact_store, published[1].id, "modal",
        "watermark.visible",
    )[1]["job_digest"]
    server.fail_posts = True
    with pytest.raises(remote_service.RemoteError):
        remote_service.run_operation(
            container.repositories, container.artifact_store, published[1].id, "modal",
            "watermark.visible", accept_remote=approved,
        )
    row = container.repositories.list("remote_jobs")[0]
    assert row["status"] == "queued"
    assert row["provider_job_id"] is None
    server.fail_posts = False
    status, data = remote_service.run_operation(
        container.repositories, container.artifact_store, published[1].id, "modal",
        "watermark.visible", accept_remote=approved,
    )
    assert status == "succeeded"
    assert data["remote_job"] == row["id"]
    assert ("POST", "/v1/jobs") in server.calls
    assert len(container.repositories.list("remote_jobs")) == 1


def test_the_approved_pipeline_reaches_the_worker(remote, published):
    server, _ = remote
    container, source = published
    remote_service.add("modal", "https://worker.example", "env:IMAGEN_TEST_TOKEN")
    approved = remote_service.run_operation(
        container.repositories, container.artifact_store, source.id, "modal",
        "watermark.invisible", pipeline="sdxl-zimage", accept_license=True,
    )[1]["job_digest"]
    remote_service.run_operation(
        container.repositories, container.artifact_store, source.id, "modal",
        "watermark.invisible", pipeline="sdxl-zimage", accept_license=True,
        accept_remote=approved,
    )
    body = server.bodies[-1]
    assert b"sdxl-zimage" in body
    assert b'"pipeline"' in body


def test_remote_runs_ignore_the_local_ram_fallback(remote, published, monkeypatch):
    server, _ = remote
    container, source = published
    remote_service.add("modal", "https://worker.example", "env:IMAGEN_TEST_TOKEN")
    # The estimate describes remote worker memory; a small local machine must not gate it.
    monkeypatch.setattr(watermark_service, "_total_memory_bytes", lambda: 1024**3)
    status, data = remote_service.run_operation(
        container.repositories, container.artifact_store, source.id, "modal",
        "watermark.invisible", pipeline="sdxl-zimage", accept_license=True,
    )
    assert status == "approval_required"
    assert data["job_digest"]


def test_a_sync_endpoint_gets_its_own_max_seconds(remote, published):
    server, _ = remote
    container, source = published
    remote_service.add("modal", "https://worker.example", "env:IMAGEN_TEST_TOKEN")
    approved = remote_service.run_operation(
        container.repositories, container.artifact_store, source.id, "modal",
        "watermark.visible",
    )[1]["job_digest"]
    remote_service.run_operation(
        container.repositories, container.artifact_store, source.id, "modal",
        "watermark.visible", accept_remote=approved,
    )
    assert b"90" in server.bodies[-1]


def test_poll_timeout_keeps_the_job_id(monkeypatch):
    class Client:
        def get(self, path):
            return httpx.Response(200, json={"status": "running"})

    ticks = {"count": 0}

    def monotonic():
        ticks["count"] += 1
        return 0 if ticks["count"] == 1 else 10**9

    monkeypatch.setattr(remote_service.time, "monotonic", monotonic)
    monkeypatch.setattr(remote_service.time, "sleep", lambda seconds: None)
    # Deliberate duck-typed double: _poll only calls get().
    payload = remote_service._poll(Client(), JOB, 5)  # pyright: ignore[reportArgumentType]
    assert payload["status"] == "unknown"
    assert payload["job_id"] == JOB


def test_resume_reports_a_running_job_as_accepted(remote, published):
    server, _ = remote
    container, _ = published
    data = _approved_submit(remote, published)
    server.job_state = "running"
    container.repositories.update(
        "remote_jobs", data["remote_job"],
        {"status": "running", "artifact_id": None},
    )
    status, resumed = remote_service.job_resume(
        container.repositories, container.artifact_store, data["remote_job"],
    )
    assert status == "accepted"
    assert resumed["job"]["status"] == "running"


def test_a_timed_out_job_can_resume_after_it_finishes(remote, published):
    container, _ = published
    data = _approved_submit(remote, published)
    container.repositories.update(
        "remote_jobs", data["remote_job"],
        {"status": "unknown", "artifact_id": None},
    )
    status, resumed = remote_service.job_resume(
        container.repositories, container.artifact_store, data["remote_job"],
    )
    assert status == "succeeded"
    assert resumed["artifact"]["id"]


def test_add_does_not_register_an_endpoint_that_fails_the_probe(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEN_TEST_TOKEN", "super-secret")
    monkeypatch.setattr(remote_service.i18n, "config_path", lambda: tmp_path / "config.json")

    def down(request):
        raise httpx.ConnectError("down", request=request)

    monkeypatch.setattr(remote_service, "TRANSPORT", httpx.MockTransport(down))
    with pytest.raises(remote_service.RemoteError):
        remote_service.add("modal", "https://worker.example", "env:IMAGEN_TEST_TOKEN")
    assert "modal" not in remote_service.remotes()


def test_matte_submits_under_its_own_operation_with_the_licence(remote, published):
    """Matting is not a watermark op: it must not borrow the raiw licence label."""
    server, _ = remote
    container, source = published
    remote_service.add("modal", "https://worker.example", "env:IMAGEN_TEST_TOKEN")

    status, data = remote_service.run_operation(
        container.repositories, container.artifact_store, source.id, "modal",
        "matte", params={"model": "birefnet-lite"},
    )
    assert status == "approval_required"
    assert data["request"]["operation"] == "matte"
    assert data["request"]["license"] == "matting:birefnet-lite:MIT"


def test_matte_rejects_an_unknown_model_before_any_upload(remote, published):
    server, _ = remote
    container, source = published
    remote_service.add("modal", "https://worker.example", "env:IMAGEN_TEST_TOKEN")
    server.calls.clear()
    with pytest.raises(ValueError, match="unknown matting model"):
        remote_service.run_operation(
            container.repositories, container.artifact_store, source.id, "modal",
            "matte", params={"model": "not-a-model"},
        )
    assert not any(method == "POST" for method, _path in server.calls)


def test_matte_publishes_an_rgba_artifact(remote, published):
    server, _ = remote
    container, source = published
    remote_service.add("modal", "https://worker.example", "env:IMAGEN_TEST_TOKEN")
    server.output = _png()
    approved = remote_service.run_operation(
        container.repositories, container.artifact_store, source.id, "modal",
        "matte", params={"model": "birefnet-lite"},
    )[1]["job_digest"]
    status, data = remote_service.run_operation(
        container.repositories, container.artifact_store, source.id, "modal",
        "matte", params={"model": "birefnet-lite"}, accept_remote=approved,
    )
    assert status == "succeeded"
    assert data["artifact"]["id"] != source.id
    assert data["artifact"]["origin_manifest"]["operation"] == "matte"


def test_job_path_resolves_private_credentials(monkeypatch):
    """`private:` refs must resolve outside the submit call, too."""
    from imagen_cli.application import remote_service as rs
    from imagen_cli.storage.private_state import PrivateStateStore

    seen = {}

    class Store:
        def read_secret(self, name):
            seen["name"] = name
            return "resolved-secret"

    record = {"endpoint": "https://w.example", "credential_ref": "private:worker-token"}
    monkeypatch.setattr(rs, "remotes", lambda: {"w": record})
    monkeypatch.setattr(rs, "TRANSPORT", httpx.MockTransport(
        lambda request: httpx.Response(200, json={"status": "queued"})
    ))
    with rs._client(record, private_store=Store()) as client:
        client.get("/v1/jobs")
    assert seen["name"] == "worker-token"
    assert PrivateStateStore is not None


def test_matte_is_refused_before_upload_when_the_worker_cannot_do_it(monkeypatch):
    """A route that exists is not a capability; refuse before spending an upload."""
    from imagen_cli.application import remote_service as rs

    record = {"endpoint": "https://w.example", "credential_ref": "env:T",
              "capabilities": {"matte": False}}
    monkeypatch.setattr(rs, "get", lambda name: record)

    class Repos:
        def get(self, table, ident):
            return {"id": ident, "sha256": "0" * 64, "save_state": "published"}

    class Store:
        def get(self, ident):
            raise AssertionError("the source must not be read before the refusal")

    with pytest.raises(ValueError, match="capabilities.matte"):
        # Deliberate doubles: the refusal must happen before either is used.
        rs.run_operation(
            Repos(), Store(), "art_x", "worker", "matte",  # pyright: ignore[reportArgumentType]
        )


def test_classify_returns_a_report_and_publishes_nothing(remote, published):
    container, source = published
    remote_service.add("modal", "https://worker.example", "env:IMAGEN_TEST_TOKEN")
    approved = remote_service.run_operation(
        container.repositories, container.artifact_store, source.id, "modal",
        "watermark.classify", params={"source": False},
    )[1]["job_digest"]
    before = len(container.repositories.list("artifacts"))
    status, data = remote_service.run_operation(
        container.repositories, container.artifact_store, source.id, "modal",
        "watermark.classify", accept_remote=approved, params={"source": False},
    )
    assert status == "succeeded"
    assert data["report"] == {"status": "cleaned"}
    assert "artifact" not in data
    assert len(container.repositories.list("artifacts")) == before


def test_a_failed_read_only_job_is_not_reported_as_success(remote, published, monkeypatch):
    container, source = published
    remote_service.add("modal", "https://worker.example", "env:IMAGEN_TEST_TOKEN")
    approved = remote_service.run_operation(
        container.repositories, container.artifact_store, source.id, "modal",
        "watermark.classify", params={"source": False},
    )[1]["job_digest"]
    monkeypatch.setattr(remote_service, "_submit", lambda *args, **kwargs: {
        "status": "failed",
        "error": {"type": "RuntimeError", "message": "the worker blew up"},
    })
    status, data = remote_service.run_operation(
        container.repositories, container.artifact_store, source.id, "modal",
        "watermark.classify", accept_remote=approved, params={"source": False},
    )
    assert status == "failed"
    assert data["error"]["type"] == "RuntimeError"
