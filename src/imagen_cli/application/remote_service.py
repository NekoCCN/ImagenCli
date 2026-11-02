"""Remote GPU endpoints: registration, approval digest, submit and fetch."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import httpx

from imagen_cli import i18n, settings
from imagen_cli.application import watermark_service
from imagen_cli.contract import RESUMABLE, JobStatus, job_digest
from imagen_cli.domain.primitive import new_id
from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.repositories import Repositories

REMOTE_NAME = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
POLL_SECONDS = 1.0
TERMINAL = {status.value for status in JobStatus} - {JobStatus.QUEUED.value,
                                                      JobStatus.RUNNING.value}
TRANSPORT: httpx.BaseTransport | None = None


class RemoteError(watermark_service.WatermarkError):
    pass


def _config() -> dict[str, Any]:
    try:
        payload = json.loads(i18n.config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save(payload: dict[str, Any]) -> None:
    path = i18n.config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def remotes() -> dict[str, dict[str, Any]]:
    payload = _config().get("remotes")
    return payload if isinstance(payload, dict) else {}


def get(name: str) -> dict[str, Any]:
    record = remotes().get(name)
    if record is None:
        raise ValueError(f"remote endpoint '{name}' is not registered")
    return record


def _store_record(name: str, record: dict[str, Any]) -> None:
    payload = _config()
    payload["remotes"] = {**remotes(), name: record}
    _save(payload)


def _endpoint(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("endpoint must be an http(s) URL")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("a remote endpoint must use https unless it is localhost")
    return value.rstrip("/")


def _credential(reference: str) -> str:
    if not reference.startswith("env:"):
        raise ValueError("credential references must use env:NAME")
    value = os.environ.get(reference.removeprefix("env:"))
    if not value:
        raise ValueError(f"credential {reference} is not set in this shell")
    return value


def _client(record: dict[str, Any], *, timeout: float = 30.0) -> httpx.Client:
    headers = {
        "Authorization": "Bearer " + _credential(str(record["credential_ref"])),
    }
    gateway = record.get("gateway_credential_ref")
    if gateway:
        headers["Salad-Api-Key"] = _credential(str(gateway))
    return httpx.Client(
        base_url=str(record["endpoint"]), headers=headers, timeout=timeout,
        transport=TRANSPORT, follow_redirects=False,
    )


def describe(record: dict[str, Any], name: str | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "endpoint": record.get("endpoint"),
        "state_mode": record.get("state_mode"),
        "gpu": record.get("gpu"),
        "worker_digest": record.get("worker_digest"),
        "raiw_version": record.get("raiw_version"),
        "imagen_cli_version": record.get("imagen_cli_version"),
    }


def _meta(record: dict[str, Any]) -> dict[str, Any]:
    try:
        with _client(record) as client:
            response = client.get("/v1/meta")
    except httpx.HTTPError as error:
        raise RemoteError(
            "failed", f"the remote connection failed: {type(error).__name__}"
        ) from error
    if response.status_code == 401:
        raise RemoteError("authentication_error", "the worker rejected the bearer token")
    if response.status_code != 200:
        raise RemoteError("failed", f"worker meta returned HTTP {response.status_code}")
    if response.status_code != 200:
        raise RemoteError("failed", f"worker meta returned HTTP {response.status_code}")
    return cast(dict[str, Any], response.json())


def _apply_meta(record: dict[str, Any], meta: dict[str, Any]) -> None:
    record.update(
        state_mode=meta.get("state_mode"),
        gpu=meta.get("gpu"),
        worker_digest=meta.get("worker_digest"),
        raiw_version=meta.get("raiw_version"),
        imagen_cli_version=meta.get("imagen_cli_version"),
        max_attempts=meta.get("max_attempts"),
        sync_max_seconds=meta.get("sync_max_seconds"),
    )


def status(name: str) -> dict[str, Any]:
    record = dict(get(name))
    meta = _meta(record)
    _apply_meta(record, meta)
    _store_record(name, record)
    return {**describe(record, name), "meta": meta}



def add(name: str, endpoint: str, credential_ref: str, *,
        gateway_credential_ref: str | None = None) -> dict[str, Any]:
    if not REMOTE_NAME.fullmatch(name):
        raise ValueError("remote names use lowercase letters, digits and hyphens")
    _credential(credential_ref)
    if gateway_credential_ref:
        _credential(gateway_credential_ref)
    record = {
        "endpoint": _endpoint(endpoint),
        "credential_ref": credential_ref,
        "gateway_credential_ref": gateway_credential_ref,
        "added_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    meta = _meta(record)
    _apply_meta(record, meta)
    _store_record(name, record)
    return {**describe(record, name), "meta": meta}


def remove(name: str) -> None:
    payload = _config()
    records = remotes()
    if name not in records:
        raise ValueError(f"remote endpoint '{name}' is not registered")
    records.pop(name)
    payload["remotes"] = records
    _save(payload)


def list_() -> list[dict[str, Any]]:
    return [describe(record, name) for name, record in sorted(remotes().items())]


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _insert_job(repositories: Repositories, **fields: Any) -> dict[str, Any]:
    job_id = new_id("rmt")
    now = _now()
    repositories.insert("remote_jobs", {
        "id": job_id, "created_at": now, "updated_at": now, **fields,
    })
    return cast(dict[str, Any], repositories.get("remote_jobs", job_id))


def _update_job(repositories: Repositories, job_id: str, **changes: Any) -> dict[str, Any]:
    changes["updated_at"] = _now()
    repositories.update("remote_jobs", job_id, changes)
    return cast(dict[str, Any], repositories.get("remote_jobs", job_id))


def jobs_list(repositories: Repositories, provider: str | None = None) -> list[dict[str, Any]]:
    rows = (repositories.list("remote_jobs", provider=provider) if provider
            else repositories.list("remote_jobs"))
    return sorted(rows, key=lambda row: str(row.get("created_at")), reverse=True)


def _remote_record(row: dict[str, Any]) -> dict[str, Any]:
    return dict(get(str(row["provider"])))


def _job_status(row: dict[str, Any], provider_job_id: str) -> dict[str, Any]:
    with _client(_remote_record(row)) as client:
        response = client.get(f"/v1/jobs/{provider_job_id}")
    if response.status_code == 404:
        return {"status": JobStatus.UNKNOWN.value, "reason": "state_not_found"}
    if response.status_code != 200:
        raise RemoteError("unknown", f"job status returned HTTP {response.status_code}")
    return cast(dict[str, Any], response.json())


def _artifact_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": row["provider"],
        "endpoint_host": row["endpoint_host"],
        "gpu": row.get("gpu") or "unknown",
        "worker_image_digest": row.get("worker_image_digest") or "unknown",
        "input_sha256": row["input_sha256"],
    }


def job_get(repositories: Repositories, job_id: str) -> dict[str, Any]:
    row = repositories.get("remote_jobs", job_id)
    if row is None:
        raise ValueError("remote job was not found")
    live: dict[str, Any] | None = None
    if row.get("provider_job_id"):
        try:
            live = _job_status(row, str(row["provider_job_id"]))
        except RemoteError as error:
            live = {"status": error.status, "error": str(error)}
        if live.get("status") and live["status"] != row.get("status"):
            row = _update_job(repositories, job_id, status=live["status"])
    return {"job": row, "live": live}


def _publish_remote_output(repositories: Repositories, store: ArtifactStore,
                           row: dict[str, Any], produced: bytes,
                           response: dict[str, Any]) -> dict[str, Any]:
    artifact = _publish(repositories, store, str(row["input_artifact_id"]), produced,
                        str(row["operation"]), str(row["provider"]),
                        _artifact_fields(row), response, None)
    _update_job(repositories, str(row["id"]), artifact_id=artifact["id"])
    return artifact


def _fetch_output(repositories: Repositories, store: ArtifactStore,
                  row: dict[str, Any]) -> dict[str, Any] | None:
    provider_job_id = row.get("provider_job_id")
    if not provider_job_id:
        return None
    with _client(_remote_record(row)) as client:
        response = client.get(f"/v1/jobs/{provider_job_id}/output")
    if response.status_code != 200:
        return None
    return _publish_remote_output(repositories, store, row, response.content, row)


def job_resume(repositories: Repositories, store: ArtifactStore,
               job_id: str) -> tuple[str, dict[str, Any]]:
    row = repositories.get("remote_jobs", job_id)
    if row is None:
        raise ValueError("remote job was not found")
    if row.get("status") not in {status.value for status in RESUMABLE} and not (
        row.get("status") == JobStatus.UNKNOWN.value and row.get("provider_job_id")
    ):
        raise ValueError("only queued, running or succeeded jobs can resume")
    if row.get("artifact_id"):
        return "succeeded", {"job": row}
    live = _job_status(row, str(row["provider_job_id"])) if row.get("provider_job_id") else {}
    if live.get("status") == JobStatus.SUCCEEDED.value:
        if row.get("operation") == "watermark.identify":
            row = _update_job(repositories, job_id, status=JobStatus.SUCCEEDED.value)
            return "succeeded", {"job": row, "report": live.get("report", {})}
        artifact = _fetch_output(repositories, store, row)
        if artifact is None:
            return "unknown", {"job": row, "error": {
                "type": "unknown", "detail": "the result is finished but the output is unavailable",
            }}
        row = repositories.get("remote_jobs", job_id)
        return "succeeded", {"job": row, "artifact": artifact}
    status = live.get("status", row.get("status"))
    row = _update_job(repositories, job_id, status=status)
    if status in {JobStatus.QUEUED.value, JobStatus.RUNNING.value}:
        return "accepted", {"job": row, "live": live}
    return str(status), {"job": row, "live": live}


def job_retry_interrupted(repositories: Repositories, store: ArtifactStore,
                          job_id: str) -> tuple[str, dict[str, Any]]:
    row = repositories.get("remote_jobs", job_id)
    if row is None:
        raise ValueError("remote job was not found")
    if row.get("status") != JobStatus.INTERRUPTED.value:
        raise ValueError("only interrupted remote jobs can be retried")
    path = store.get(str(row["input_artifact_id"]))
    if path is None:
        raise ValueError("the source artifact is no longer available")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != row["input_sha256"]:
        raise ValueError("the source artifact bytes changed; the retry is refused")
    request = json.loads(str(row["request_canonical"]))
    record = _remote_record(row)
    response = _post(record, request, data)
    job_id_new = response.get("job_id")
    status = response.get("status", JobStatus.QUEUED.value)
    row = _update_job(repositories, job_id,
                      provider_job_id=str(job_id_new) if job_id_new else row.get("provider_job_id"),
                      status=status, payload={"retried": True})
    return "accepted", {"job": row, "live": response}


def job_cancel(repositories: Repositories, job_id: str) -> tuple[str, dict[str, Any]]:
    row = repositories.get("remote_jobs", job_id)
    if row is None:
        raise ValueError("remote job was not found")
    if not row.get("provider_job_id"):
        row = _update_job(repositories, job_id, status=JobStatus.CANCELED.value)
        return "succeeded", {"job": row}
    with _client(_remote_record(row)) as client:
        response = client.delete(f"/v1/jobs/{row['provider_job_id']}")
    if response.status_code == 404:
        row = _update_job(repositories, job_id, status=JobStatus.UNKNOWN.value)
        return "unknown", {"job": row}
    reported = response.json().get("status", JobStatus.UNKNOWN.value)
    status = reported if reported in {s.value for s in JobStatus} else JobStatus.UNKNOWN.value
    row = _update_job(repositories, job_id, status=status)
    return ("succeeded" if status == JobStatus.CANCELED.value else str(status)), {"job": row}


def _job_fields(record: dict[str, Any], name: str, operation: str, pipeline: str,
                params: dict[str, Any], source_sha: str, upload_bytes: int,
                max_seconds: int) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "provider": name,
        "endpoint_host": str(urlparse(str(record["endpoint"])).hostname or ""),
        "gpu": str(record.get("gpu") or "unknown"),
        "operation": operation,
        "pipeline": pipeline,
        "normalized_params": params,
        "input_sha256": source_sha,
        "upload_bytes": upload_bytes,
        "max_seconds": max_seconds,
        "worker_image_digest": str(record.get("worker_digest") or "unknown"),
        "license": "raiw:" + (pipeline or "metadata"),
    }


def _publish(repositories: Repositories, store: ArtifactStore, source: str,
             produced: bytes, operation: str, remote: str,
             fields: dict[str, Any], response: dict[str, Any],
             session: str | None = None) -> dict[str, Any]:
    # Resolve the destination first: a published but unlinked artifact would be unreachable.
    session_id = watermark_service._session(repositories, source, session)
    identity = new_id("art")
    store.stage(identity, produced, origin_manifest={
        "kind": "remote",
        "operation": operation,
        "source": source,
        "source_sha256": fields["input_sha256"],
        "remote": remote,
        "remote_status": response.get("status"),
        "engine": {
            "kind": "remote",
            "provider": fields["provider"],
            "endpoint_host": fields["endpoint_host"],
            "gpu": fields["gpu"],
            "worker_digest": fields["worker_image_digest"],
            "attempts": response.get("attempts", 1),
        },
    })
    store.publish(identity)
    repositories.insert("artifact_links", {
        "session_id": session_id,
        "artifact_id": identity, "role": "output",
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    })
    return cast(dict[str, Any], repositories.get("artifacts", identity))


def run_operation(
    repositories: Repositories,
    store: ArtifactStore,
    artifact: str,
    remote_name: str,
    operation: str,
    *,
    accept_remote: str | None = None,
    session: str | None = None,
    pipeline: str | None = None,
    params: dict[str, Any] | None = None,
    max_seconds: int | None = None,
    accept_license: bool = False,
    memory_mb: int | None = None,
    budget_approval: str | None = None,
) -> tuple[str, dict[str, Any]]:
    from PIL import Image

    record = dict(get(remote_name))
    row = repositories.get("artifacts", artifact)
    path = store.get(artifact)
    if row is None or path is None or row["save_state"] != "published":
        raise ValueError("watermark source must be a published artifact")
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != row["sha256"]:
        raise ValueError("source artifact bytes changed")
    with Image.open(path) as opened:
        size = opened.size
    # Resolve the destination before the upload; a published orphan would be unreachable.
    session_id = None if operation in {"watermark.identify", "watermark.classify"} \
        else watermark_service._session(repositories, artifact, session)
    if operation in {"watermark.invisible", "watermark.all"}:
        pipeline = pipeline or "qwen-zimage"
        params = {**(params or {}), "pipeline": pipeline}
        if not accept_license:
            raise watermark_service.WatermarkError(
                "approval_required",
                f"pipeline {pipeline} uses "
                f"{watermark_service.PIPELINE_LICENSES[pipeline]}; "
                "rerun with --accept-license after reviewing it",
                {"license": {"pipeline": pipeline,
                             "license": watermark_service.PIPELINE_LICENSES[pipeline]}},
            )
        # The estimate describes the worker's memory; local RAM only applies when configured.
        if memory_mb is not None or settings.memory_limit() is not None:
            watermark_service.budget_preflight(
                digest, size, pipeline, approval=budget_approval,
                memory_mb=memory_mb, cpu_offload=bool((params or {}).get("cpu_offload")),
            )
    if max_seconds is None:
        # A sync endpoint answers inline and bounds the request itself.
        max_seconds = 900
        if record.get("state_mode") == "single-replica-sync":
            max_seconds = int(record.get("sync_max_seconds") or 90)
    fields = _job_fields(record, remote_name, operation, pipeline or "",
                         params or {}, digest, len(data), max_seconds)
    requested = job_digest(fields)
    if accept_remote != requested:
        return "approval_required", {
            "remote": describe(record, remote_name),
            "job_digest": requested,
            "request": fields,
            "error": {
                "type": "approval_required",
                "detail": i18n.t(
                    "Remote work uploads the image to this endpoint. Review the request, "
                    "then rerun with --accept-remote " + requested,
                    "远程任务会把图片上传到该端点。确认请求后，重跑并加上 --accept-remote "
                    + requested,
                ),
            },
        }
    previous = repositories.list("remote_jobs", job_digest=requested)
    reusable: dict[str, Any] | None = None
    if previous:
        row = previous[0]
        if row.get("artifact_id"):
            return "succeeded", {
                "artifact": repositories.get("artifacts", row["artifact_id"]),
                "remote_job": row["id"], "job_digest": requested, "replayed": True,
            }
        if row.get("provider_job_id") or row.get("status") in TERMINAL:
            return "accepted", {"job": row, "remote_job": row["id"],
                                "job_digest": requested, "replayed": True}
        # A queued row with no job id means the earlier POST never answered. It is
        # only a local intent, and the worker deduplicates by digest, so resubmit.
        reusable = row
    submission = {
        "schema_version": 1,
        "operation": operation,
        "job_digest": requested,
        "max_seconds": max_seconds,
        "params": params or {},
        "input_sha256": digest,
    }
    job_row = reusable or _insert_job(
        repositories,
        provider=remote_name,
        provider_job_id=None,
        queue_id=None,
        endpoint_host=fields["endpoint_host"],
        operation=operation,
        input_artifact_id=artifact,
        input_sha256=digest,
        request_canonical=json.dumps(submission, sort_keys=True, separators=(",", ":")),
        job_digest=requested,
        pipeline=pipeline or "",
        gpu=fields["gpu"],
        max_seconds=max_seconds,
        license=fields["license"],
        worker_image_digest=fields["worker_image_digest"],
        status=JobStatus.QUEUED.value,
        artifact_id=None,
        payload={"remote": remote_name},
    )
    response = _submit(record, operation, params or {}, data,
                       digest, requested, max_seconds)
    updates: dict[str, Any] = {"status": _contract_status(response.get("status"))}
    if response.get("job_id"):
        updates["provider_job_id"] = str(response["job_id"])
    _update_job(repositories, str(job_row["id"]), **updates)
    if operation == "watermark.identify":
        return "succeeded", {"remote": describe(record, remote_name),
                             "report": response.get("report", {}),
                             "job_digest": requested,
                             "remote_job": job_row["id"]}
    if response.get("status") == JobStatus.UNKNOWN.value:
        return "unknown", {"remote": describe(record, remote_name), "request": fields,
                           "remote_job": job_row["id"],
                           "error": {"type": "unknown",
                                     "detail": "the remote result is not determined"}}
    produced = response.get("output_bytes")
    if not produced:
        _update_job(repositories, str(job_row["id"]), status=JobStatus.FAILED.value)
        return "failed", {"remote": describe(record, remote_name),
                          "remote_job": job_row["id"],
                          "error": {"type": "failed", "detail": "worker returned no output"}}
    artifact_row = _publish(repositories, store, artifact, produced, operation,
                            remote_name, fields, response, session_id)
    _update_job(repositories, str(job_row["id"]),
                artifact_id=artifact_row["id"], status=JobStatus.SUCCEEDED.value)
    return "succeeded", {
        "artifact": artifact_row,
        "remote": describe(record, remote_name),
        "job_digest": requested,
        "remote_job": job_row["id"],
        "status": response.get("status"),
        "report": response.get("report", {}),
    }


def _contract_status(value: Any) -> str:
    if value in {"succeeded", "cleaned", "partial", "unvalidated", "no_watermark"}:
        return JobStatus.SUCCEEDED.value
    return str(value) if str(value) in {status.value for status in JobStatus} \
        else JobStatus.UNKNOWN.value


def _submit(record: dict[str, Any], operation: str,
            params: dict[str, Any], data: bytes, input_sha256: str,
            request_digest: str, max_seconds: int) -> dict[str, Any]:
    submission = {
        "schema_version": 1,
        "operation": operation,
        "job_digest": request_digest,
        "max_seconds": max_seconds,
        "params": params,
        "input_sha256": input_sha256,
    }
    return _post(record, submission, data)


def _post(record: dict[str, Any], submission: dict[str, Any],
          data: bytes) -> dict[str, Any]:
    max_seconds = int(submission["max_seconds"])
    sync_mode = record.get("state_mode") == "single-replica-sync"
    timeout = max_seconds + 30 if sync_mode else 60
    try:
        with _client(record, timeout=timeout) as client:
            try:
                response = client.post(
                    "/v1/jobs", data={"job": json.dumps(submission)},
                    files={"input": ("input.bin", data, "application/octet-stream")},
                )
            except httpx.ConnectError:
                raise RemoteError(
                    "failed", "the request was not submitted; the same command may be retried"
                ) from None
            except httpx.TimeoutException:
                raise RemoteError(
                    "unknown", "the request may have been submitted; do not retry blindly"
                ) from None
            if response.status_code == 401:
                raise RemoteError("authentication_error", "the worker rejected the bearer token")
            if response.status_code == 429:
                raise RemoteError("conflict", "the endpoint is busy with another job")
            if response.status_code in {413, 422}:
                raise RemoteError("validation_error",
                                  str(response.json().get("detail", "request rejected")))
            if response.status_code not in {200, 202}:
                raise RemoteError("failed", f"worker returned HTTP {response.status_code}")
            payload = cast(dict[str, Any], response.json())
            if response.status_code == 200:
                if payload.get("output", {}).get("base64"):
                    payload["output_bytes"] = base64.b64decode(payload["output"]["base64"])
                return payload
            job_id = payload["job_id"]
            return _poll(client, job_id, max_seconds)
    except RemoteError:
        raise
    except httpx.HTTPError as error:
        raise RemoteError(
            "unknown", f"the remote connection failed: {type(error).__name__}"
        ) from error


def _poll(client: httpx.Client, job_id: str, max_seconds: int) -> dict[str, Any]:
    deadline = time.monotonic() + max_seconds + 60
    while time.monotonic() < deadline:
        response = client.get(f"/v1/jobs/{job_id}")
        if response.status_code == 401:
            raise RemoteError("authentication_error", "the worker rejected the bearer token")
        if response.status_code == 404:
            raise RemoteError("unknown", "state_not_found")
        if response.status_code != 200:
            raise RemoteError("unknown", f"job status returned HTTP {response.status_code}")
        payload = cast(dict[str, Any], response.json())
        if payload.get("status") in TERMINAL:
            if payload.get("status") == JobStatus.SUCCEEDED.value:
                output = client.get(f"/v1/jobs/{job_id}/output")
                if output.status_code == 200:
                    payload["output_bytes"] = output.content
            return payload
        time.sleep(POLL_SECONDS)
    # The id is kept so a timed-out job can still be resumed instead of lost.
    return {"status": JobStatus.UNKNOWN.value, "reason": "poll_timeout", "job_id": job_id}
