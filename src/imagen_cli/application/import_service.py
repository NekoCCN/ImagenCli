"""Staged, fail-closed import of version-1 portable archives.

Every byte, path and relationship is validated in a private staging directory
before the first database write; the whole remapped history then commits once.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any
from zipfile import BadZipFile, ZipFile

from pydantic import ValidationError

from imagen_cli.application.export_service import (
    ARCHIVE_VERSION,
    ASSET_PREFIX,
    MANIFEST_NAME,
    _rows,
    public_value,
)
from imagen_cli.domain.enums import ArtifactRole
from imagen_cli.domain.models import ArtifactReference, Job, Session, Turn
from imagen_cli.domain.primitive import new_id
from imagen_cli.storage.artifacts import MAX_BYTES, ArtifactStore, inspect_media
from imagen_cli.storage.repositories import Repositories
from imagen_cli.storage.safety import (
    relative_path,
    safe_path,
    secure_directory,
    validate_manifest,
    validate_zip,
)

MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_FILES = 10_000
MAX_ARCHIVE_FILE_BYTES = MAX_BYTES
MAX_ARCHIVE_TOTAL_BYTES = 1024 * 1024 * 1024

_SESSION_KEYS = ("id", "name", "intent", "version", "created_at")
_TURN_KEYS = (
    "id",
    "session_id",
    "instruction",
    "operation",
    "resolved_profile_snapshot",
    "model",
    "parameters",
    "input_manifest",
    "expected_session_version",
    "job_id",
    "creation_state",
    "publication_state",
    "created_at",
)
_JOB_KEYS = (
    "id",
    "turn_id",
    "state",
    "dispatch_state",
    "remote_state",
    "local_save_state",
    "requested_count",
    "actual_count",
    "accepted_maybe",
    "created_at",
    "updated_at",
)
_ARTIFACT_KEYS = (
    "id",
    "path",
    "media_type",
    "width",
    "height",
    "byte_size",
    "sha256",
    "origin_manifest",
    "created_at",
)
_REFERENCE_KEYS = ("session_id", "turn_id", "artifact_id", "role", "index")
_MANIFEST_KEYS = {
    "archive_version",
    "exported_at",
    "session",
    "turns",
    "jobs",
    "artifacts",
    "references",
    "native_contexts",
    "omissions",
}


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise ValueError("non-finite JSON number")


def _strict_json(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("manifest too large")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("manifest is not UTF-8") from error
    if "\x00" in text:
        raise ValueError("manifest contains NUL")
    try:
        value = json.loads(
            text, object_pairs_hook=_pairs, parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as error:
        raise ValueError("manifest is not valid JSON") from error
    if not isinstance(value, dict):
        raise ValueError("manifest must be an object")
    return value


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _records(value: Any, name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{name} must be a list of objects")
    return value


def _require(record: dict[str, Any], keys: tuple[str, ...], name: str) -> None:
    missing = set(keys) - set(record)
    if missing:
        raise ValueError(f"{name} is missing required fields")


def _asset_path(value: Any) -> str:
    if not isinstance(value, str) or not value.startswith(ASSET_PREFIX):
        raise ValueError("asset path must be a normal relative path")
    relative_path(value)
    parts = PurePosixPath(value).parts
    if len(parts) != 2 or parts[0] != ASSET_PREFIX.rstrip("/"):
        raise ValueError("asset path must be a single file below assets/")
    return value


def _validated(manifest: dict[str, Any]) -> dict[str, Any]:
    if set(manifest) - _MANIFEST_KEYS:
        # In particular, approvals/plans are never importable execution rights.
        raise ValueError("unexpected manifest field")
    if type(manifest.get("archive_version")) is not int:
        raise ValueError("archive version is required")
    if manifest["archive_version"] != ARCHIVE_VERSION:
        raise ValueError("unsupported archive version")
    validate_manifest(manifest)

    session = _mapping(manifest.get("session"), "session")
    _require(session, _SESSION_KEYS, "session")
    source_id = session["id"]
    if not isinstance(source_id, str) or not source_id:
        raise ValueError("session identity is required")

    turns = _records(manifest.get("turns"), "turns")
    jobs = _records(manifest.get("jobs"), "jobs")
    artifacts = _records(manifest.get("artifacts"), "artifacts")
    references = _records(manifest.get("references"), "references")
    for turn in turns:
        _require(turn, _TURN_KEYS, "turn")
    for job in jobs:
        _require(job, _JOB_KEYS, "job")
    for artifact in artifacts:
        _require(artifact, _ARTIFACT_KEYS, "artifact")
    for reference in references:
        _require(reference, _REFERENCE_KEYS, "reference")

    turn_ids = {turn["id"] for turn in turns}
    job_ids = {job["id"] for job in jobs}
    artifact_ids = {artifact["id"] for artifact in artifacts}
    if len(turn_ids) != len(turns) or len(job_ids) != len(jobs):
        raise ValueError("duplicate identity")
    if len(artifact_ids) != len(artifacts):
        raise ValueError("duplicate identity")

    job_turns: set[str] = set()
    for turn in turns:
        if turn["session_id"] != source_id:
            raise ValueError("turn belongs to another session")
        parent = turn.get("parent_turn_id")
        if parent is not None and not isinstance(parent, str):
            raise ValueError("invalid turn parent")
        if parent is not None and parent not in turn_ids:
            raise ValueError("turn parent is missing from the archive")
        if not isinstance(turn["job_id"], str) or turn["job_id"] not in job_ids:
            raise ValueError("turn job is missing from the archive")
    for job in jobs:
        if not isinstance(job["turn_id"], str) or job["turn_id"] not in turn_ids:
            raise ValueError("job belongs to a missing turn")
        if job["turn_id"] in job_turns:
            raise ValueError("duplicate job for turn")
        job_turns.add(job["turn_id"])
    if job_turns != turn_ids:
        raise ValueError("every turn requires exactly one job")

    current = session.get("current_turn_id")
    if current is not None:
        if not isinstance(current, str) or current not in turn_ids:
            raise ValueError("current turn is missing from the archive")
        selected = next(turn for turn in turns if turn["id"] == current)
        if (
            selected["publication_state"] != "published"
            or selected["creation_state"] != "succeeded"
        ):
            raise ValueError("current turn is not a published success")

    paths: dict[str, str] = {}
    normalized_paths: set[str] = set()
    for artifact in artifacts:
        path = _asset_path(artifact["path"])
        if path in paths:
            raise ValueError("duplicate asset path")
        normalized = unicodedata.normalize("NFC", path).casefold()
        if normalized in normalized_paths:
            raise ValueError("duplicate normalized asset path")
        normalized_paths.add(normalized)
        paths[path] = artifact["id"]
        if (
            not isinstance(artifact["media_type"], str)
            or not artifact["media_type"]
            or type(artifact["width"]) is not int
            or artifact["width"] < 1
            or type(artifact["height"]) is not int
            or artifact["height"] < 1
            or type(artifact["byte_size"]) is not int
            or artifact["byte_size"] < 0
            or not isinstance(artifact["sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"]) is None
            or not isinstance(artifact["origin_manifest"], dict)
            or not isinstance(artifact["created_at"], str)
        ):
            raise ValueError("invalid artifact metadata")
        producer_turn = artifact.get("producer_turn_id")
        producer_job = artifact.get("producer_job_id")
        if producer_turn is not None and not isinstance(producer_turn, str):
            raise ValueError("invalid artifact producer")
        if producer_job is not None and not isinstance(producer_job, str):
            raise ValueError("invalid artifact producer")
        if producer_turn is not None and producer_turn not in turn_ids:
            raise ValueError("artifact producer turn is missing")
        if producer_job is not None and producer_job not in job_ids:
            raise ValueError("artifact producer job is missing")
        if producer_job is not None and producer_turn is None:
            raise ValueError("artifact producer job requires a turn")
        if (
            producer_turn is None
            and not artifact.get("origin_manifest")
        ):
            raise ValueError("imported artifact requires origin metadata")

    composite: set[tuple[str, str, int]] = set()
    for reference in references:
        if reference["session_id"] != source_id:
            raise ValueError("reference belongs to another session")
        if not isinstance(reference["turn_id"], str) or reference["turn_id"] not in turn_ids:
            raise ValueError("reference turn is missing")
        if (
            not isinstance(reference["artifact_id"], str)
            or reference["artifact_id"] not in artifact_ids
        ):
            raise ValueError("reference artifact is missing")
        if reference["role"] not in {role.value for role in ArtifactRole}:
            raise ValueError("invalid reference role")
        index = reference["index"]
        if type(index) is not int or index < 0:
            raise ValueError("invalid reference index")
        key = (reference["turn_id"], reference["role"], index)
        if key in composite:
            raise ValueError("duplicate reference slot")
        composite.add(key)

    return {
        "session": session,
        "turns": turns,
        "jobs": jobs,
        "artifacts": artifacts,
        "references": references,
        "native_contexts": _records(manifest.get("native_contexts"), "native_contexts"),
        "omissions": _records(manifest.get("omissions"), "omissions"),
        "asset_paths": paths,
    }


def _unique_name(repositories: Repositories, wanted: str) -> str:
    existing = {row["name"] for row in _rows(repositories, "sessions")}
    if wanted not in existing:
        return wanted
    base = f"{wanted} (imported)"
    candidate, counter = base, 2
    while candidate in existing:
        candidate = f"{base} {counter}"
        counter += 1
    return candidate


def _extract_assets(
    archive: ZipFile,
    artifacts: list[dict[str, Any]],
    staging: Path,
    *,
    max_file_size: int,
) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for position, artifact in enumerate(sorted(artifacts, key=lambda item: item["id"])):
        with archive.open(artifact["path"]) as source:
            data = source.read(max_file_size + 1)
        if len(data) > max_file_size:
            raise ValueError("asset too large")
        if len(data) != artifact["byte_size"]:
            raise ValueError("asset size mismatch")
        if hashlib.sha256(data).hexdigest() != artifact["sha256"]:
            raise ValueError("asset hash mismatch")
        measured = inspect_media(data)
        for key in ("media_type", "width", "height", "byte_size", "sha256"):
            if measured[key] != artifact[key]:
                raise ValueError("asset metadata mismatch")
        target = staging / f"{position}.bin"
        fd = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        files[artifact["id"]] = target
    return files


def _order_turns(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    remaining = list(turns)
    inserted: set[str] = set()
    ordered: list[dict[str, Any]] = []
    while remaining:
        progressed = False
        for turn in list(remaining):
            parent = turn.get("parent_turn_id")
            if parent is None or parent in inserted:
                ordered.append(turn)
                inserted.add(turn["id"])
                remaining.remove(turn)
                progressed = True
        if not progressed:
            raise ValueError("cyclic turn parent")
    return ordered


def _commit(
    repositories: Repositories,
    store: ArtifactStore,
    validated: dict[str, Any],
    asset_files: dict[str, Path],
) -> dict[str, Any]:
    session = validated["session"]
    turns = validated["turns"]
    jobs = validated["jobs"]
    artifacts = validated["artifacts"]
    references = validated["references"]
    source_id = session["id"]
    omissions = [public_value(item) for item in validated["omissions"]]

    turn_map = {turn["id"]: new_id("trn") for turn in turns}
    job_map = {job["id"]: new_id("job") for job in jobs}
    artifact_map = {artifact["id"]: new_id("art") for artifact in artifacts}
    session_id = new_id("ses")

    origin_session = session.get("origin_session_id")
    if origin_session is not None or session.get("origin_turn_id") is not None:
        omissions.append({"kind": "external_origin", "source_session_id": origin_session})

    created: list[str] = []
    try:
        with repositories.transaction():
            record = Session.model_validate({
                "id": session_id,
                "name": _unique_name(repositories, session["name"]),
                "intent": session["intent"],
                "default_profile_id": session.get("default_profile_id"),
                "default_model": session.get("default_model"),
                "current_turn_id": None,
                "version": session["version"],
                "created_at": session["created_at"],
                "deleted_at": None,
                "origin_session_id": None,
                "origin_turn_id": None,
            }).model_dump(mode="json")
            repositories.insert("sessions", record)

            for turn in _order_turns(turns):
                input_manifest = []
                for entry in turn.get("input_manifest") or []:
                    if not isinstance(entry, dict):
                        raise ValueError("input manifest entry must be an object")
                    identity = entry.get("artifact_id")
                    if identity not in artifact_map:
                        omissions.append({
                            "kind": "external_reference",
                            "artifact_id": identity,
                            "turn_id": turn["id"],
                        })
                        continue
                    input_manifest.append({
                        **public_value(entry), "artifact_id": artifact_map[identity],
                    })
                validated_turn = Turn.model_validate({
                    "id": turn_map[turn["id"]],
                    "session_id": session_id,
                    "parent_turn_id": turn_map.get(turn.get("parent_turn_id")),
                    "instruction": turn["instruction"],
                    "operation": turn["operation"],
                    "resolved_profile_snapshot": public_value(
                        turn["resolved_profile_snapshot"]
                    ),
                    "model": turn["model"],
                    "parameters": public_value(turn["parameters"]),
                    "input_manifest": input_manifest,
                    "expected_session_version": turn["expected_session_version"],
                    "job_id": job_map[turn["job_id"]],
                    "creation_state": turn["creation_state"],
                    "publication_state": turn["publication_state"],
                    "created_at": turn["created_at"],
                    "finished_at": turn.get("finished_at"),
                }).model_dump(mode="json")
                validated_turn["job_id"] = None
                repositories.insert("turns", validated_turn)

            for job in jobs:
                retry = job.get("retry_of_job_id")
                validated_job = Job.model_validate_json(json.dumps({
                    "id": job_map[job["id"]],
                    "turn_id": turn_map[job["turn_id"]],
                    "batch_id": None,
                    "plan_id": None,
                    "retry_of_job_id": job_map.get(retry),
                    "state": job["state"],
                    "dispatch_state": job["dispatch_state"],
                    "remote_state": job["remote_state"],
                    "local_save_state": job["local_save_state"],
                    "requested_count": job["requested_count"],
                    "actual_count": job["actual_count"],
                    "accepted_maybe": job["accepted_maybe"],
                    "remote_id": None,
                    "private_locator_ref": None,
                    "executor_id": None,
                    "lease_token": None,
                    "lease_expires_at": None,
                    "cancel_intent": None,
                    "cancel_local": None,
                    "cancel_remote": None,
                    "cost": job.get("cost"),
                    "created_at": job["created_at"],
                    "updated_at": job["updated_at"],
                }))
                repositories.insert("jobs", validated_job.model_dump(mode="json"))
            for turn in turns:
                repositories.update(
                    "turns", turn_map[turn["id"]], {"job_id": job_map[turn["job_id"]]},
                )

            for artifact in sorted(artifacts, key=lambda item: item["id"]):
                identity = artifact_map[artifact["id"]]
                origin = public_value(artifact["origin_manifest"])
                # Keep durable provenance even when the CLI result is not retained.
                origin["portable_origin"] = {
                    "session_id": source_id,
                    "turn_id": artifact.get("producer_turn_id"),
                    "artifact_id": artifact["id"],
                }
                # Track both target names before staging so a partial write is cleaned up.
                created.extend([f"{identity}.image", f"{identity}.staging"])
                row = store.stage(
                    identity,
                    asset_files[artifact["id"]].read_bytes(),
                    producer_turn_id=turn_map.get(artifact.get("producer_turn_id")),
                    producer_job_id=job_map.get(artifact.get("producer_job_id")),
                    origin_manifest=origin,
                )
                created.extend([row["managed_relpath"], row["staging_relpath"]])
                store.publish(identity)
                repositories.update("artifacts", identity, {"created_at": artifact["created_at"]})

            for reference in references:
                validated_reference = ArtifactReference.model_validate({
                    "session_id": session_id,
                    "turn_id": turn_map[reference["turn_id"]],
                    "artifact_id": artifact_map[reference["artifact_id"]],
                    "role": reference["role"],
                    "index": reference["index"],
                })
                repositories.insert(
                    "artifact_references", validated_reference.model_dump(mode="json"),
                )

            if session.get("current_turn_id") is not None:
                repositories.update("sessions", session_id, {
                    "current_turn_id": turn_map[session["current_turn_id"]],
                })
    except BaseException:
        for relpath in created:
            try:
                safe_path(store.root, relpath).unlink(missing_ok=True)
            except (OSError, ValueError):
                pass
        raise

    native = [
        {
            "provider": item.get("provider"),
            "family": item.get("family"),
            "model": item.get("model"),
            "source_turn_id": turn_map.get(item.get("source_turn_id")),
            "availability": "missing",
        }
        for item in validated["native_contexts"]
    ]
    from imagen_cli.application.postprocess_service import replay_status

    postprocess_replay = {}
    for artifact in artifacts:
        compatibility = replay_status(artifact["origin_manifest"])
        if compatibility is not None:
            postprocess_replay[artifact_map[artifact["id"]]] = compatibility
    return {
        "session": repositories.get("sessions", session_id),
        "postprocess_replay": postprocess_replay,
        "turns": len(turns),
        "artifacts": len(artifacts),
        "references": len(references),
        "origin_map": {
            "session": {source_id: session_id},
            "turns": turn_map,
            "jobs": job_map,
            "artifacts": artifact_map,
        },
        "omissions": omissions,
        "native_contexts_missing": native,
    }


def import_archive(
    repositories: Repositories,
    store: ArtifactStore,
    archive_path: str | Path,
    *,
    max_file_size: int = MAX_ARCHIVE_FILE_BYTES,
    max_total_size: int = MAX_ARCHIVE_TOTAL_BYTES,
) -> dict[str, Any]:
    """Validate the whole archive in staging, then import it in one transaction."""
    path = Path(archive_path)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    staging = secure_directory(Path(tempfile.mkdtemp(prefix=".import-", dir=store.root.parent)))
    try:
        try:
            archive = ZipFile(path)
        except (BadZipFile, OSError) as error:
            raise ValueError("archive is not a valid ZIP") from error
        with archive:
            names = validate_zip(
                archive,
                max_files=MAX_ARCHIVE_FILES,
                max_file_size=max_file_size,
                max_total_size=max_total_size,
            )
            if MANIFEST_NAME not in names:
                raise ValueError("archive has no manifest")
            with archive.open(MANIFEST_NAME) as source:
                raw = source.read(MAX_MANIFEST_BYTES + 1)
            validated = _validated(_strict_json(raw))
            expected = {MANIFEST_NAME, *validated["asset_paths"]}
            if set(names) != expected:
                raise ValueError("archive contains unexpected entries")
            asset_files = _extract_assets(
                archive, validated["artifacts"], staging, max_file_size=max_file_size,
            )
        try:
            return _commit(repositories, store, validated, asset_files)
        except (sqlite3.IntegrityError, ValidationError) as error:
            raise ValueError("archive conflicts with existing history") from error
    finally:
        shutil.rmtree(staging, ignore_errors=True)
