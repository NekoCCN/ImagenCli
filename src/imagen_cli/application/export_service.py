"""Version-1 portable session export; secrets and private state never leave.

The archive is deliberately manifest-first: importing code can validate every
relationship and file fact before touching storage.
"""

from __future__ import annotations

import json
import math
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.repositories import Repositories
from imagen_cli.storage.safety import PRIVATE_FIELDS

ARCHIVE_VERSION = 1
MANIFEST_NAME = "manifest.json"
ASSET_PREFIX = "assets/"
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)

# `credential_ref` is a reference, not a secret, but the portable archive never
# needs it: imported history cannot execute anything.
_EXTRA_PRIVATE = {"credential_ref", "download_url", "native_parts"}
_PRIVATE_KEYS = {
    re.sub(r"[^a-z0-9]", "", key.casefold()) for key in (*PRIVATE_FIELDS, *_EXTRA_PRIVATE)
}

_JOB_FIELDS = (
    "id",
    "turn_id",
    "state",
    "dispatch_state",
    "remote_state",
    "local_save_state",
    "requested_count",
    "actual_count",
    "accepted_maybe",
    "retry_of_job_id",
    "cost",
    "created_at",
    "updated_at",
)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def public_value(value: Any) -> Any:
    """Recursively copy JSON-safe values, dropping every private field."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
            if normalized in _PRIVATE_KEYS:
                continue
            result[key] = public_value(child)
        return result
    if isinstance(value, (list, tuple)):
        return [public_value(child) for child in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite JSON number")
    if value is None or isinstance(value, str | int | bool):
        return value
    raise ValueError("unsupported JSON value")


def _rows(repositories: Repositories, table: str, **filters: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    while True:
        page = repositories.list(table, limit=1000, offset=len(rows), **filters)
        rows.extend(page)
        if len(page) < 1000:
            return rows


def _asset_name(identity: str) -> str:
    if re.fullmatch(r"art_[0-9a-f]{32}", identity) is None:
        raise ValueError("invalid artifact identity")
    return f"{ASSET_PREFIX}{identity}.image"


def _job_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    return {key: public_value(row[key]) for key in _JOB_FIELDS if key in row}


def _omission_key(item: dict[str, Any]) -> tuple[str, str]:
    kind = str(item.get("kind", ""))
    target = str(
        item.get("artifact_id") or item.get("turn_id") or item.get("source_session_id", "")
    )
    return kind, target


def build_manifest(
    repositories: Repositories,
    store: ArtifactStore,
    session_id: str,
    *,
    exported_at: str | None = None,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Return the manifest and asset bytes without writing anything."""
    session = repositories.get("sessions", session_id)
    if session is None or session.get("deleted_at") is not None:
        raise KeyError(session_id)
    turns = sorted(
        _rows(repositories, "turns", session_id=session_id),
        key=lambda row: (row["created_at"], row["id"]),
    )
    turn_ids = {row["id"] for row in turns}
    jobs: dict[str, dict[str, Any]] = {}
    for turn in turns:
        job = repositories.get("jobs", turn["job_id"])
        if job is None:
            raise ValueError("turn has no persisted job")
        jobs[job["id"]] = _job_snapshot(job)

    references = sorted(
        _rows(repositories, "artifact_references", session_id=session_id),
        key=lambda row: (row["turn_id"], row["role"], row["index"]),
    )
    selected: set[str] = {ref["artifact_id"] for ref in references}
    for turn in turns:
        for entry in turn.get("input_manifest") or []:
            identity = entry.get("artifact_id")
            if isinstance(identity, str):
                selected.add(identity)
    selected |= {
        row["id"] for row in _rows(repositories, "artifacts")
        if row.get("producer_turn_id") in turn_ids
    }

    omissions: list[dict[str, Any]] = []
    assets: dict[str, bytes] = {}
    artifact_rows: dict[str, dict[str, Any]] = {}
    for identity in sorted(selected):
        row = repositories.get("artifacts", identity)
        if row is None:
            omissions.append({"kind": "external_reference", "artifact_id": identity})
            continue
        if row["save_state"] != "published":
            omissions.append({"kind": "missing_file", "artifact_id": identity})
            continue
        try:
            path = store.get(identity)
        except (FileNotFoundError, OSError, ValueError):
            path = None
        if path is None:
            omissions.append({"kind": "missing_file", "artifact_id": identity})
            continue
        assets[_asset_name(identity)] = path.read_bytes()
        artifact_rows[identity] = row

    exported_artifacts: list[dict[str, Any]] = []
    for identity in sorted(artifact_rows):
        row = artifact_rows[identity]
        producer_turn = row.get("producer_turn_id")
        producer_job = row.get("producer_job_id")
        if producer_turn is not None and producer_turn not in turn_ids:
            omissions.append({"kind": "external_producer", "artifact_id": identity})
            producer_turn, producer_job = None, None
        elif producer_job is not None and producer_job not in jobs:
            omissions.append({"kind": "external_producer", "artifact_id": identity})
            producer_turn, producer_job = None, None
        exported_artifacts.append({
            "id": identity,
            "path": _asset_name(identity),
            "producer_turn_id": producer_turn,
            "producer_job_id": producer_job,
            "media_type": row["media_type"],
            "width": row["width"],
            "height": row["height"],
            "byte_size": row["byte_size"],
            "sha256": row["sha256"],
            "origin_manifest": public_value(row.get("origin_manifest") or {}),
            "created_at": row.get("created_at") or session["created_at"],
        })

    kept = set(artifact_rows)
    exported_references = []
    for ref in references:
        if ref["artifact_id"] not in kept:
            omissions.append({
                "kind": "external_reference",
                "turn_id": ref["turn_id"],
                "artifact_id": ref["artifact_id"],
                "role": ref["role"],
            })
            continue
        exported_references.append({
            "session_id": session_id,
            "turn_id": ref["turn_id"],
            "artifact_id": ref["artifact_id"],
            "role": ref["role"],
            "index": ref["index"],
        })

    exported_turns = []
    for turn in turns:
        input_manifest = []
        for entry in turn.get("input_manifest") or []:
            identity = entry.get("artifact_id")
            if identity not in kept:
                omissions.append({
                    "kind": "external_reference",
                    "turn_id": turn["id"],
                    "artifact_id": identity,
                })
                continue
            input_manifest.append(public_value(entry))
        exported_turns.append({
            "id": turn["id"],
            "session_id": turn["session_id"],
            "parent_turn_id": turn.get("parent_turn_id"),
            "instruction": turn["instruction"],
            "operation": turn["operation"],
            "resolved_profile_snapshot": public_value(turn["resolved_profile_snapshot"]),
            "model": turn["model"],
            "parameters": public_value(turn["parameters"]),
            "input_manifest": input_manifest,
            "expected_session_version": turn["expected_session_version"],
            "job_id": turn["job_id"],
            "creation_state": turn["creation_state"],
            "publication_state": turn["publication_state"],
            "created_at": turn["created_at"],
            "finished_at": turn.get("finished_at"),
        })

    # Native state is intentionally never read; mark every record as missing.
    native_contexts = []
    for row in sorted(
        _rows(repositories, "native_contexts"), key=lambda item: item["id"],
    ):
        if row.get("source_turn_id") not in turn_ids:
            continue
        native_contexts.append({
            "origin_id": row["id"],
            "provider": row["provider"],
            "family": row["family"],
            "profile_identity": row["profile_identity"],
            "model": row["model"],
            "format_version": row["format_version"],
            "expires_at": row.get("expires_at"),
            "source_turn_id": row["source_turn_id"],
            "availability": "missing",
        })
        omissions.append({
            "kind": "native_context_missing",
            "source_turn_id": row["source_turn_id"],
        })

    manifest = {
        "archive_version": ARCHIVE_VERSION,
        "exported_at": exported_at or _now(),
        "session": public_value({
            key: session.get(key)
            for key in (
                "id",
                "name",
                "intent",
                "default_profile_id",
                "default_model",
                "current_turn_id",
                "version",
                "created_at",
                "deleted_at",
                "origin_session_id",
                "origin_turn_id",
            )
        }),
        "turns": exported_turns,
        "jobs": [jobs[identity] for identity in sorted(jobs)],
        "artifacts": exported_artifacts,
        "references": exported_references,
        "native_contexts": native_contexts,
        "omissions": sorted(omissions, key=_omission_key),
    }
    return manifest, assets


def _write_entry(archive: ZipFile, name: str, data: bytes) -> None:
    info = ZipInfo(name, date_time=_ZIP_TIME)
    info.compress_type = ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    archive.writestr(info, data)


def export_session(
    repositories: Repositories,
    store: ArtifactStore,
    session_id: str,
    destination: str | Path,
    *,
    exported_at: str | None = None,
) -> dict[str, Any]:
    """Write a portable archive to a new path; existing files are never replaced."""
    manifest, assets = build_manifest(
        repositories, store, session_id, exported_at=exported_at,
    )
    encoded = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    target = Path(destination)
    if not target.name or "\x00" in str(target) or not target.parent.is_dir():
        raise ValueError("archive destination must be an existing directory")
    fd = os.open(
        target,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            with ZipFile(stream, "w", ZIP_DEFLATED, compresslevel=6) as archive:
                _write_entry(archive, MANIFEST_NAME, encoded)
                for name in sorted(assets):
                    _write_entry(archive, name, assets[name])
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return {
        "archive": str(target.absolute()),
        "session_id": session_id,
        "turns": len(manifest["turns"]),
        "artifacts": len(manifest["artifacts"]),
        "references": len(manifest["references"]),
        "omissions": manifest["omissions"],
    }
