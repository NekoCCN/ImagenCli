"""Shared remote worker HTTP contract (schema_version 1).

One app serves Modal, Salad queue, and Salad single-replica-sync. The state
mode changes durability and the POST response, never the job semantics.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

from imagen_cli.contract import JobStatus, reject_client_paths

SCHEMA_VERSION = 1
STATE_MODES = {"shared-volume", "queue", "single-replica-sync"}
SYNC_OPERATIONS = {"watermark.identify", "watermark.metadata", "watermark.visible",
                   "matte"}
# Long diffusion rebuilds: only a queue or shared-volume endpoint can carry them.
ASYNC_OPERATIONS = {"watermark.invisible", "watermark.all"}
SYNC_MAX_SECONDS = 90
SYNC_MAX_BYTES = 64 * 1024 * 1024

Executor = Callable[[str, dict[str, Any], bytes], dict[str, Any]]


@dataclass
class WorkerConfig:
    token: str
    state_mode: str = "shared-volume"
    jobs_root: Path = Path("/jobs")
    worker_version: str = "0.0.0"
    imagen_cli_version: str = "unknown"
    raiw_version: str | None = None
    providers: tuple[str, ...] = ()
    gpu: str | None = None
    sync_max_seconds: int = SYNC_MAX_SECONDS
    sync_max_bytes: int = SYNC_MAX_BYTES
    max_attempts: int = 4
    worker_digest: str | None = None
    queue: dict[str, str] | None = None


class VolumeStore:
    """Atomic per-job files; Modal volumes can be briefly unavailable after reload."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = threading.Lock()

    def _directory(self, job_id: str) -> Path:
        return self.root / job_id

    def write(self, job_id: str, record: dict[str, Any], output: bytes | None) -> None:
        # Windows 不允许替换仍被读取的文件，读写使用同一把锁。
        with self._lock:
            directory = self._directory(job_id)
            directory.mkdir(parents=True, exist_ok=True)
            if output is not None:
                self._replace(directory / "output.bin", output)
            # 先保存输出，再发布成功记录，避免状态可见时结果文件还不存在。
            self._replace(directory / "record.json", json.dumps(record).encode("utf-8"))

    @staticmethod
    def _replace(target: Path, data: bytes) -> None:
        # The job thread and a cancel request may write at once; never share a temp file.
        temporary = target.with_name(f"{target.name}.{os.urandom(8).hex()}.part")
        try:
            with temporary.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def read(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            path = self._directory(job_id) / "record.json"
            return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))

    def output(self, job_id: str) -> bytes:
        with self._lock:
            return (self._directory(job_id) / "output.bin").read_bytes()


def _capabilities(config: WorkerConfig) -> dict[str, bool]:
    """Report what this image can actually run, not what its routes accept.

    A route existing is not evidence the operation will work: the probe imports
    the backend and checks the pinned weights, so a missing extra or weight is
    reported the same way a real job would fail. Route names live in
    ``operations`` so the two questions stay separate.
    """
    try:
        from imagen_cli.remote.worker_cli import handle

        probe = handle({"schema_version": SCHEMA_VERSION, "operation": "probe"})
        return {str(k): bool(v) for k, v in (probe.get("capabilities") or {}).items()}
    except Exception:  # noqa: BLE001 - an unprobeable image reports nothing as ready
        return {}


def _cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001 - optional backend
        return False


def _matting_providers() -> list[str]:
    try:
        from imagen_cli.application import matting

        return list(matting.available_providers())
    except Exception:  # noqa: BLE001 - optional backend
        return []


def _detect_providers() -> tuple[str, ...]:
    providers: list[str] = []
    if _cuda_available():
        providers.append("cuda")
    for provider in _matting_providers():
        name = provider.replace("ExecutionProvider", "").lower()
        if name and name not in providers:
            providers.append(name)
    if not providers:
        providers.append("cpu")
    return tuple(providers)


def _operations(config: WorkerConfig) -> list[str]:
    allowed = SYNC_OPERATIONS if config.state_mode == "single-replica-sync" \
        else SYNC_OPERATIONS | ASYNC_OPERATIONS
    return sorted(allowed)


def _default_execute(job: dict[str, Any], source: bytes) -> dict[str, Any]:
    from imagen_cli.remote.worker_cli import handle

    extension = mimetypes.guess_extension(str(job.get("media_type", "image/png"))) or ".png"
    operation = str(job["operation"])
    with tempfile.TemporaryDirectory(prefix="imagen-worker-") as directory:
        input_path = Path(directory) / ("input" + extension)
        input_path.write_bytes(source)
        response = handle({
            "schema_version": SCHEMA_VERSION,
            "imagen_cli_version": job.get("imagen_cli_version"),
            "operation": operation,
            "params": job.get("params") or {},
            "input": str(input_path),
            "output": str(Path(directory) / ("output" + extension))
            if operation != "watermark.identify" else None,
        })
        output = None
        if operation != "watermark.identify":
            candidate = Path(directory) / ("output" + extension)
            output = candidate.read_bytes() if candidate.is_file() else None
    response["output_bytes"] = output
    return response


def _media_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def create_app(config: WorkerConfig, execute: Executor | None = None) -> FastAPI:
    if config.state_mode not in STATE_MODES:
        raise ValueError("unknown state mode")
    store = VolumeStore(config.jobs_root)
    runner = execute or (lambda job_id, job, source: _default_execute(job, source))
    busy = threading.Lock()
    known: dict[str, dict[str, Any]] = {}
    digests: dict[str, str] = {}
    app = FastAPI(title="imagen-worker", docs_url=None, redoc_url=None)

    def authorize(authorization: str | None = Header(default=None)) -> None:
        expected = "Bearer " + config.token
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="unauthorized")

    def _record(job_id: str) -> dict[str, Any] | None:
        return known.get(job_id)

    def _load(job_id: str) -> dict[str, Any] | None:
        """Busy or half-written volume reads are retried once, never read as 404."""
        for attempt in range(2):
            try:
                return store.read(job_id)
            except FileNotFoundError:
                if job_id not in known:
                    return None
                time.sleep(0.05)
            except (OSError, json.JSONDecodeError):
                if attempt == 0:
                    time.sleep(0.05)
        if job_id in known:
            return {**known[job_id], "status": JobStatus.UNKNOWN.value,
                    "reason": "volume_unavailable"}
        return None

    def _missing() -> JSONResponse:
        return JSONResponse(
            {"status": JobStatus.UNKNOWN.value, "reason": "state_not_found"}, status_code=404,
        )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> dict[str, Any]:
        missing = []
        if not config.token:
            missing.append("token")
        if not config.jobs_root.exists() and config.state_mode == "shared-volume":
            missing.append("jobs_volume")
        return {"ready": not missing, "missing": missing}

    @app.get("/v1/meta", dependencies=[Depends(authorize)])
    def meta() -> dict[str, Any]:
        data: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "capabilities": _capabilities(config),
            "operations": _operations(config),
            "worker_version": config.worker_version,
            "imagen_cli_version": config.imagen_cli_version,
            "raiw_version": config.raiw_version,
            "state_mode": config.state_mode,
            "auth": "bearer",
            "providers": list(config.providers or _detect_providers()),
            "gpu": config.gpu,
            "worker_digest": config.worker_digest,
            "max_attempts": config.max_attempts if config.state_mode == "queue" else 1,
            "sync_max_seconds": config.sync_max_seconds
            if config.state_mode == "single-replica-sync" else None,
        }
        if config.queue:
            data["queue"] = config.queue
        return data

    @app.post("/v1/jobs", dependencies=[Depends(authorize)])
    def submit(  # noqa: A002
        job: str = Form(...), input: UploadFile = File(...),  # noqa: B008
    ) -> Any:
        try:
            payload = json.loads(job)
            if not isinstance(payload, dict):
                raise ValueError("job must be a JSON object")
            reject_client_paths(payload)
            if payload.get("schema_version") != SCHEMA_VERSION:
                raise ValueError("unsupported schema_version")
            operation = payload.get("operation")
            if operation not in SYNC_OPERATIONS | ASYNC_OPERATIONS:
                raise ValueError("unknown operation")
            digest = payload.get("job_digest")
            if not isinstance(digest, str) or len(digest) != 64:
                raise ValueError("job_digest is required")
            max_seconds = payload.get("max_seconds")
            if type(max_seconds) is not int or max_seconds < 1:
                raise ValueError("max_seconds is required")
        except (json.JSONDecodeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        source = input.file.read()
        claimed = payload.get("input_sha256")
        if claimed and claimed != hashlib.sha256(source).hexdigest():
            raise HTTPException(status_code=422, detail="input digest mismatch")
        if config.state_mode == "single-replica-sync":
            if operation not in SYNC_OPERATIONS:
                raise HTTPException(status_code=422, detail="sync_unsupported")
            if max_seconds > config.sync_max_seconds:
                raise HTTPException(status_code=422, detail="sync_unsupported")
            if len(source) > config.sync_max_bytes:
                raise HTTPException(status_code=413, detail="payload_too_large")
        existing = digests.get(digest)
        if existing is not None:
            prior = _load(existing) or known.get(existing) or {}
            body: dict[str, Any] = {
                "job_id": existing,
                "status": prior.get("status", JobStatus.QUEUED.value),
            }
            if config.state_mode == "single-replica-sync":
                # The sync contract answers inline, including a replay of a finished job.
                body["report"] = prior.get("report", {})
                body["report_status"] = prior.get("report_status")
                output = dict(prior.get("output") or {})
                if "base64" not in output:
                    try:
                        output["base64"] = base64.b64encode(store.output(existing)).decode()
                    except (OSError, FileNotFoundError):
                        pass
                body["output"] = output
            return JSONResponse(
                body, status_code=200 if config.state_mode == "single-replica-sync" else 202,
            )
        if not busy.acquire(blocking=False):
            raise HTTPException(status_code=429, detail="one job at a time")
        job_id = "job_" + os.urandom(16).hex()
        record: dict[str, Any] = {
            "job_id": job_id, "status": JobStatus.QUEUED.value,
            "operation": operation, "job_digest": digest,
            "max_seconds": max_seconds, "params": payload.get("params") or {},
            "attempts": 1, "max_attempts": config.max_attempts,
            "created_at": time.time(),
        }
        known[job_id] = record
        digests[digest] = job_id

        def execute() -> bytes | None:
            try:
                record["status"] = JobStatus.RUNNING.value
                result = runner(job_id, payload, source)
                output = result.pop("output_bytes", None)
                record.update(result)
                reported = str(result.get("status", JobStatus.SUCCEEDED.value))
                record["report_status"] = reported
                if reported in {"succeeded", "cleaned", "partial", "unvalidated", "no_watermark"}:
                    record["status"] = JobStatus.SUCCEEDED.value
                elif reported in {status.value for status in JobStatus}:
                    record["status"] = reported
                else:
                    record["status"] = JobStatus.FAILED.value
                if output is not None:
                    record["output"] = {
                        "sha256": hashlib.sha256(output).hexdigest(),
                        "media_type": _media_type(output),
                        "bytes": len(output),
                    }
                store.write(job_id, record, output)
                return output
            except Exception as error:  # noqa: BLE001 - worker failures are structured
                record.update(status=JobStatus.FAILED.value,
                              error={"type": type(error).__name__, "message": str(error)})
                store.write(job_id, record, None)
                return None
            finally:
                busy.release()

        if config.state_mode in {"shared-volume", "queue"}:
            store.write(job_id, record, None)
            threading.Thread(target=execute, daemon=True).start()
            return JSONResponse({"job_id": job_id, "status": record["status"]}, status_code=202)

        output = execute()
        if output is not None:
            record["output"] = {
                **record.get("output", {}),
                "base64": base64.b64encode(output).decode(),
            }
        return record

    @app.get("/v1/jobs/{job_id}", dependencies=[Depends(authorize)])
    def get_job(job_id: str) -> Any:
        record = _load(job_id)
        if record is None:
            return _missing()
        return record

    @app.get("/v1/jobs/{job_id}/output", dependencies=[Depends(authorize)])
    def get_output(job_id: str) -> Any:
        record = _load(job_id)
        if record is None:
            return _missing()
        try:
            data = store.output(job_id)
        except (OSError, FileNotFoundError):
            if job_id in known:
                return JSONResponse(
                    {"status": JobStatus.UNKNOWN.value, "reason": "output_unavailable"},
                    status_code=404,
                )
            return _missing()
        return Response(content=data, media_type=record.get("output", {}).get("media_type"))

    @app.delete("/v1/jobs/{job_id}", dependencies=[Depends(authorize)])
    def cancel(job_id: str) -> Any:
        record = _load(job_id)
        if record is None:
            return _missing()
        if record.get("status") in {JobStatus.QUEUED.value, JobStatus.RUNNING.value}:
            record["status"] = JobStatus.CANCELED.value
            record["cancel"] = "requested"
            store.write(job_id, record, None)
            if job_id in known:
                known[job_id]["status"] = JobStatus.CANCELED.value
        return record

    return app


def from_environment() -> FastAPI:
    """Uvicorn factory for container images; configuration is environment-only."""
    queue = None
    if os.environ.get("SALAD_QUEUE_NAME"):
        queue = {
            "organization": os.environ.get("SALAD_QUEUE_ORGANIZATION", ""),
            "project": os.environ.get("SALAD_QUEUE_PROJECT", ""),
            "name": os.environ["SALAD_QUEUE_NAME"],
        }
    raw_providers = os.environ.get("IMAGEN_WORKER_PROVIDERS")
    providers = (
        tuple(item for item in raw_providers.split(",") if item)
        if raw_providers is not None
        else _detect_providers()
    )
    return create_app(WorkerConfig(
        token=os.environ["IMAGEN_WORKER_TOKEN"],
        state_mode=os.environ.get("IMAGEN_WORKER_STATE_MODE", "shared-volume"),
        jobs_root=Path(os.environ.get("IMAGEN_JOBS_ROOT", "/jobs")),
        worker_version=os.environ.get("IMAGEN_WORKER_VERSION", "0.0.0"),
        imagen_cli_version=os.environ.get("IMAGEN_CLI_VERSION", "unknown"),
        raiw_version=os.environ.get("IMAGEN_RAIW_VERSION"),
        providers=providers,
        gpu=os.environ.get("IMAGEN_MODAL_GPU"),
        worker_digest=os.environ.get("IMAGEN_WORKER_DIGEST"),
        queue=queue,
    ))
