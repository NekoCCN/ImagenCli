"""Worker HTTP contract: auth, state modes, one-job bound, volume read protocol."""

import base64
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytestmark = pytest.mark.filterwarnings(
    "ignore:Using `httpx` with `starlette.testclient` is deprecated"
)
from fastapi.testclient import TestClient  # noqa: E402

from deploy.worker.app import VolumeStore, WorkerConfig, create_app  # noqa: E402

AUTH = {"Authorization": "Bearer secret"}
DIGEST = "a" * 64
JOB_ID = "job_0123456789ab4def8abc0123456789ab"
PNG = b"\x89PNG\r\n\x1a\npayload"


def job(**overrides):
    payload = {
        "schema_version": 1,
        "operation": "watermark.visible",
        "job_digest": DIGEST,
        "max_seconds": 30,
        "params": {},
    }
    payload.update(overrides)
    return payload


def build(tmp_path, mode="single-replica-sync", execute=None, **overrides):
    root = tmp_path / "jobs"
    root.mkdir(parents=True, exist_ok=True)
    config = WorkerConfig(
        token="secret", state_mode=mode, jobs_root=root,
        worker_version="1.2.3", imagen_cli_version="0.1.17",
        raiw_version="0.53.0", providers=("cuda",), gpu="L4", **overrides,
    )
    return TestClient(create_app(config, execute=execute))


def post(client, payload=None, data=PNG):
    return client.post(
        "/v1/jobs", headers=AUTH,
        data={"job": json.dumps(payload or job())},
        files={"input": ("in.png", data, "image/png")},
    )


def cleaned(job_id, payload, source):
    return {"status": "cleaned", "report": {"marks": []}, "output_bytes": PNG,
            "engine": {"provider": "cuda"}}


def wait_for(client, job_id):
    """Queue workers finish on a thread; tests must observe the terminal record."""
    record = {}
    for _ in range(100):
        record = client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()
        if record["status"] in {"succeeded", "failed", "canceled"}:
            return record
        time.sleep(0.02)
    return record


def test_volume_write_waits_for_an_active_record_reader(tmp_path, monkeypatch):
    store = VolumeStore(tmp_path)
    store.write(JOB_ID, {"status": "queued"}, None)
    reading = threading.Event()
    release = threading.Event()
    writing = threading.Event()
    original = Path.read_text

    def slow_read(path, *args, **kwargs):
        if path.name != "record.json":
            return original(path, *args, **kwargs)
        with path.open(encoding="utf-8") as handle:
            reading.set()
            assert release.wait(5)
            return handle.read()

    def write():
        writing.set()
        store.write(JOB_ID, {"status": "succeeded"}, PNG)

    monkeypatch.setattr(Path, "read_text", slow_read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reader = pool.submit(store.read, JOB_ID)
        try:
            assert reading.wait(2)
            writer = pool.submit(write)
            assert writing.wait(2)
            with pytest.raises(TimeoutError):
                writer.result(timeout=0.1)
        finally:
            release.set()
        assert reader.result(timeout=2) == {"status": "queued"}
        writer.result(timeout=2)
    assert store.read(JOB_ID) == {"status": "succeeded"}
    assert store.output(JOB_ID) == PNG


def test_volume_does_not_publish_success_if_output_cannot_be_saved(tmp_path, monkeypatch):
    store = VolumeStore(tmp_path)
    store.write(JOB_ID, {"status": "queued"}, None)
    replace = store._replace

    def fail_output(target, data):
        if target.name == "output.bin":
            raise OSError("disk full")
        replace(target, data)

    monkeypatch.setattr(store, "_replace", fail_output)
    with pytest.raises(OSError, match="disk full"):
        store.write(JOB_ID, {"status": "succeeded"}, PNG)
    assert store.read(JOB_ID) == {"status": "queued"}


def test_health_and_readiness_are_public_and_meta_needs_auth(tmp_path):
    client = build(tmp_path)
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json()["ready"] is True
    assert client.get("/v1/meta").status_code == 401
    meta = client.get("/v1/meta", headers=AUTH).json()
    assert meta["schema_version"] == 1
    assert meta["state_mode"] == "single-replica-sync"
    assert meta["auth"] == "bearer"
    assert meta["sync_max_seconds"] == 90


def test_jobs_and_output_require_the_bearer_token(tmp_path):
    client = build(tmp_path, mode="queue")
    response = client.post(
        "/v1/jobs", data={"job": json.dumps(job())},
        files={"input": ("in.png", PNG, "image/png")},
    )
    assert response.status_code == 401
    assert client.get(f"/v1/jobs/{JOB_ID}").status_code == 401
    assert client.get(f"/v1/jobs/{JOB_ID}/output").status_code == 401


def test_sync_mode_returns_the_result_inline(tmp_path):
    client = build(tmp_path, execute=cleaned)
    response = post(client)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "succeeded"
    assert data["report_status"] == "cleaned"
    assert base64.b64decode(data["output"]["base64"]) == PNG
    assert data["output"]["sha256"] == hashlib.sha256(PNG).hexdigest()


@pytest.mark.parametrize("overrides", [
    {"operation": "watermark.invisible"},
    {"max_seconds": 91},
])
def test_sync_mode_rejects_work_it_cannot_bound(tmp_path, overrides):
    client = build(tmp_path, execute=cleaned)
    response = post(client, job(**overrides))
    assert response.status_code == 422
    assert response.json()["detail"] == "sync_unsupported"


def test_shared_volume_returns_before_the_job_finishes(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def slow(job_id, payload, source):
        started.set()
        release.wait(5)
        return cleaned(job_id, payload, source)

    client = build(tmp_path, mode="shared-volume", execute=slow)
    response = post(client)
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    assert started.wait(2)
    record = client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()
    assert record["status"] in {"queued", "running"}
    release.set()
    for _ in range(100):
        record = client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()
        if record["status"] == "succeeded":
            break
        time.sleep(0.02)
    assert record["status"] == "succeeded"
    assert record["report_status"] == "cleaned"


def test_a_repeated_digest_in_sync_mode_returns_the_output(tmp_path):
    client = build(tmp_path, execute=cleaned)
    assert post(client).status_code == 200
    again = post(client)
    assert again.status_code == 200
    assert base64.b64decode(again.json()["output"]["base64"]) == PNG
    assert again.json()["report_status"] == "cleaned"


def test_queue_mode_records_attempts_and_maps_the_status(tmp_path):
    client = build(tmp_path, mode="queue", execute=cleaned)
    response = post(client)
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    record = client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()
    for _ in range(100):
        if record["status"] == "succeeded":
            break
        time.sleep(0.02)
        record = client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()
    assert record["status"] == "succeeded"
    assert record["report_status"] == "cleaned"
    assert record["attempts"] == 1
    assert record["max_attempts"] == 4
    meta = client.get("/v1/meta", headers=AUTH).json()
    assert meta["state_mode"] == "queue"
    assert meta["max_attempts"] == 4


def test_queue_mode_returns_before_the_job_finishes(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def slow(job_id, payload, source):
        started.set()
        release.wait(5)
        return cleaned(job_id, payload, source)

    client = build(tmp_path, mode="queue", execute=slow)
    response = post(client)
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    assert started.wait(2)
    record = client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()
    assert record["status"] in {"queued", "running"}
    release.set()
    for _ in range(100):
        record = client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()
        if record["status"] == "succeeded":
            break
        time.sleep(0.02)
    assert record["status"] == "succeeded"



def test_an_unknown_job_is_state_not_found(tmp_path):
    client = build(tmp_path)
    response = client.get(f"/v1/jobs/{JOB_ID}", headers=AUTH)
    assert response.status_code == 404
    assert response.json() == {"status": "unknown", "reason": "state_not_found"}


def test_client_supplied_paths_are_rejected(tmp_path):
    client = build(tmp_path, execute=cleaned)
    response = post(client, job(input="/etc/passwd"))
    assert response.status_code == 422


def test_the_same_digest_never_runs_twice(tmp_path):
    calls = []

    def counting(job_id, payload, source):
        calls.append(job_id)
        return cleaned(job_id, payload, source)

    client = build(tmp_path, mode="queue", execute=counting)
    first = post(client)
    second = post(client)
    assert first.json()["job_id"] == second.json()["job_id"]
    assert len(calls) == 1


def test_a_second_concurrent_job_gets_429(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def blocking(job_id, payload, source):
        entered.set()
        assert release.wait(5)
        return cleaned(job_id, payload, source)

    client = build(tmp_path, mode="queue", execute=blocking)
    worker = threading.Thread(target=post, args=(client,))
    worker.start()
    assert entered.wait(5)
    response = post(client, job(job_digest="b" * 64))
    assert response.status_code == 429
    release.set()
    worker.join(5)


def test_output_endpoint_returns_the_bytes(tmp_path):
    client = build(tmp_path, mode="queue", execute=cleaned)
    job_id = post(client).json()["job_id"]
    assert wait_for(client, job_id)["status"] == "succeeded"
    response = client.get(f"/v1/jobs/{job_id}/output", headers=AUTH)
    assert response.status_code == 200
    assert response.content == PNG
    assert response.headers["content-type"].startswith("image/png")


def test_cancel_is_best_effort_and_keeps_the_record(tmp_path):
    client = build(tmp_path, mode="queue", execute=cleaned)
    job_id = post(client).json()["job_id"]
    assert wait_for(client, job_id)["status"] == "succeeded"
    response = client.delete(f"/v1/jobs/{job_id}", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"


def test_a_busy_volume_read_is_unknown_never_state_not_found(tmp_path, monkeypatch):
    client = build(tmp_path, mode="queue", execute=cleaned)
    job_id = post(client).json()["job_id"]

    def busy(self, identity):
        raise OSError("volume busy")

    monkeypatch.setattr(VolumeStore, "read", busy)
    response = client.get(f"/v1/jobs/{job_id}", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["status"] == "unknown"
    assert response.json()["reason"] == "volume_unavailable"


def test_matte_is_accepted_inline_and_returns_output(tmp_path):
    """A sync endpoint must accept matte and answer with the produced bytes."""
    seen = {}

    def execute(job_id, payload, source):
        seen["operation"] = payload["operation"]
        return {"status": "succeeded", "report": {"model": "birefnet-lite"},
                "output_bytes": PNG}

    client = build(tmp_path, execute=execute)
    response = post(client, job(operation="matte", params={"model": "birefnet-lite"}))
    assert response.status_code == 200
    assert seen["operation"] == "matte"
    body = response.json()
    assert body["status"] == "succeeded"
    assert base64.b64decode(body["output"]["base64"]) == PNG


def test_unknown_operations_are_still_rejected(tmp_path):
    client = build(tmp_path)
    response = post(client, job(operation="watermark.not_real"))
    assert response.status_code == 422


def test_meta_reports_capabilities_and_operations(tmp_path):
    """A client must be able to tell an accepted route from a working one."""
    client = build(tmp_path)
    meta = client.get("/v1/meta", headers=AUTH).json()
    assert "matte" in meta["capabilities"]
    assert "matte" in meta["operations"]


def test_sync_mode_advertises_only_operations_it_will_accept(tmp_path):
    client = build(tmp_path, mode="single-replica-sync")
    meta = client.get("/v1/meta", headers=AUTH).json()
    assert "matte" in meta["operations"]
    assert "watermark.invisible" not in meta["operations"]


def test_from_environment_detects_cpu_when_no_cuda(monkeypatch, tmp_path):
    from deploy.worker import app

    monkeypatch.setenv("IMAGEN_WORKER_TOKEN", "secret")
    monkeypatch.setenv("IMAGEN_JOBS_ROOT", str(tmp_path / "jobs"))
    monkeypatch.delenv("IMAGEN_WORKER_PROVIDERS", raising=False)
    monkeypatch.setattr(app, "_cuda_available", lambda: False)
    monkeypatch.setattr(app, "_matting_providers", lambda: ["CPUExecutionProvider"])

    test_app = app.from_environment()
    client = TestClient(test_app)
    meta = client.get("/v1/meta", headers={"Authorization": "Bearer secret"}).json()
    assert "cuda" not in meta["providers"]
    assert "cpu" in meta["providers"]



def test_sync_mode_advertises_erase_and_classify(tmp_path):
    client = build(tmp_path, mode="single-replica-sync")
    meta = client.get("/v1/meta", headers=AUTH).json()
    assert "watermark.erase" in meta["operations"]
    assert "watermark.classify" in meta["operations"]
    assert "watermark.invisible" not in meta["operations"]


def test_erase_is_accepted_inline_and_returns_the_output(tmp_path):
    client = build(tmp_path, execute=cleaned)
    response = post(client, job(operation="watermark.erase",
                                params={"regions": ["0,0,4,4"]}))
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "succeeded"
    assert data["output"]["sha256"] == hashlib.sha256(PNG).hexdigest()


def test_classify_returns_a_report_and_no_output(tmp_path):
    def classified(job_id, payload, source):
        return {"status": "succeeded", "report": {"pixels": "photo"}}

    client = build(tmp_path, execute=classified)
    response = post(client, job(operation="watermark.classify"))
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "succeeded"
    assert data["report"] == {"pixels": "photo"}
    assert "output" not in data


def test_cuda_is_available_without_torch(monkeypatch):
    import sys

    from deploy.worker import app as worker_app

    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setattr(worker_app, "_onnx_cuda_available", lambda: True)
    assert worker_app._cuda_available() is True

    monkeypatch.setattr(worker_app, "_onnx_cuda_available", lambda: False)
    assert worker_app._cuda_available() is False
