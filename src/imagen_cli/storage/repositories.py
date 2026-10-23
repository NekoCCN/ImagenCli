"""Relational repositories with short, caller-composable SQLite transactions.

Structural relationships are SQL columns/FKs; entity detail fields use JSON, not
an untyped entity bucket. Domain validation remains the application boundary.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from imagen_cli.storage.safety import PRIVATE_FIELDS, relative_path, validate_manifest

FIELDS = {
    "sessions": (
        "id name intent default_profile_id default_model current_turn_id version "
        "created_at deleted_at origin_session_id origin_turn_id"
    ),
    "turns": (
        "id session_id parent_turn_id instruction operation resolved_profile_snapshot model "
        "parameters input_manifest expected_session_version job_id creation_state "
        "publication_state created_at finished_at"
    ),
    "artifacts": (
        "id producer_turn_id producer_job_id media_type width height byte_size sha256 "
        "managed_relpath staging_relpath save_state origin_manifest created_at"
    ),
    "artifact_references": "session_id turn_id artifact_id role index",
    "jobs": (
        "id turn_id batch_id plan_id state dispatch_state remote_state local_save_state "
        "requested_count actual_count accepted_maybe remote_id private_locator_ref "
        "executor_id lease_token lease_expires_at cancel_intent cancel_local cancel_remote "
        "cost created_at updated_at retry_of_job_id worker_queued error"
    ),
    "batches": "id child_job_ids concurrency summary_state created_at",
    "capabilities": (
        "id provider family model model_revision operation support_status parameter_schema "
        "semantic_mapping input_limits transport_kind continuation_strategy "
        "idempotency_contract cancel_contract sources checked_at offline_evidence "
        "live_evidence digest"
    ),
    "profiles": (
        "id name provider family base_url model_default credential_ref trust_policy revision"
    ),
    "native_contexts": (
        "id provider family profile_identity model format_version private_relpath sha256 "
        "expires_at availability source_turn_id"
    ),
    "plans": (
        "id session_id parent_turn_id expected_session_version target_snapshot operation model "
        "normalized_parameters prompt_snapshot input_hashes portable_context capability_digest "
        "context_loss plan_digest created_at expires_at"
    ),
    "approvals": "id plan_id plan_digest state approved_at execution_job_id expires_at",
    "events": "id job_id job_seq event_type timestamp safe_payload",
    "provider_models": (
        "id profile_id model_id display_name fetched_at source_url evidence"
    ),
    "artifact_links": "session_id artifact_id role created_at",
    "workers": "id pid boot_identity state heartbeat_at start_nonce",
}
PRIVATE = PRIVATE_FIELDS | {"lease_token", "staging_relpath"}


def _public(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _public(child) for key, child in value.items() if key not in PRIVATE}
    if isinstance(value, list):
        return [_public(child) for child in value]
    return value


class Repositories:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def _columns(self, table: str) -> set[str]:
        if table not in FIELDS:
            raise ValueError("unknown repository")
        return {row[1] for row in self.connection.execute(f'PRAGMA table_info("{table}")')}

    @contextmanager
    def transaction(self) -> Iterator["Repositories"]:
        if self.connection.in_transaction:
            name = "s" + uuid4().hex
            self.connection.execute(f"SAVEPOINT {name}")
            try:
                yield self
                self.connection.execute(f"RELEASE {name}")
            except BaseException:
                self.connection.execute(f"ROLLBACK TO {name}")
                self.connection.execute(f"RELEASE {name}")
                raise
        else:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise

    def _validate(self, table: str, data: dict[str, Any]) -> None:
        if table not in FIELDS or set(data) - set(FIELDS[table].split()):
            raise ValueError("unknown entity field")
        if any(
            key.casefold().replace("-", "_")
            in {"api_key", "access_token", "download_url", "native_parts"}
            for key in data
        ):
            raise ValueError("private field forbidden")
        json.dumps(data, allow_nan=False)
        for field in (
            "version",
            "expected_session_version",
            "requested_count",
            "actual_count",
            "byte_size",
            "width",
            "height",
            "index",
            "concurrency",
            "job_seq",
        ):
            if field in data and data[field] is not None:
                if type(data[field]) is not int or data[field] < 0:
                    raise ValueError("invalid nonnegative integer")
        for field in ("managed_relpath", "staging_relpath", "private_relpath"):
            if data.get(field) is not None:
                relative_path(data[field])
        if table in {"profiles", "events", "plans", "capabilities"}:
            validate_manifest(data)
        if table == "plans":
            for field in ("created_at", "expires_at"):
                value = data.get(field)
                if not isinstance(value, str) or datetime.fromisoformat(value).tzinfo is None:
                    raise ValueError("plan requires timezone-aware TTL timestamps")
        if table == "artifacts" and data.get("save_state") == "published":
            if any(
                not data.get(key)
                for key in ("media_type", "width", "height", "byte_size", "sha256")
            ):
                raise ValueError("published artifact needs measured media metadata")
        if table == "batches" and "child_job_ids" in data:
            if len(set(data["child_job_ids"])) != len(data["child_job_ids"]):
                raise ValueError("duplicate child job")
            for job in data["child_job_ids"]:
                if self.get("jobs", job) is None:
                    raise ValueError("missing child job")
        if table == "jobs" and data.get("batch_id") is not None:
            child = self.connection.execute(
                "SELECT 1 FROM batch_children WHERE batch_id=? AND job_id=?",
                (data["batch_id"], data["id"]),
            ).fetchone()
            if child is None:
                raise ValueError("job must be assigned through batch creation")
        if table == "sessions":
            origin_session = data.get("origin_session_id")
            origin_turn = data.get("origin_turn_id")
            if origin_session is not None and self.get("sessions", origin_session) is None:
                raise ValueError("origin session missing")
            if origin_turn is not None:
                source = self.get("turns", origin_turn)
                if source is None or source["session_id"] != origin_session:
                    raise ValueError("origin turn does not belong to origin session")
        if table == "artifacts" and data.get("producer_turn_id") and data.get("producer_job_id"):
            job = self.get("jobs", data["producer_job_id"])
            if job is None or job["turn_id"] != data["producer_turn_id"]:
                raise ValueError("artifact producer job does not belong to turn")
        if table == "plans" and data.get("parent_turn_id") and data.get("session_id"):
            parent = self.get("turns", data["parent_turn_id"])
            if parent is None or parent["session_id"] != data["session_id"]:
                raise ValueError("plan parent belongs to another session")
        if table == "artifact_references":
            turn_id = data.get("turn_id")
            session_id = data.get("session_id")
            artifact_id = data.get("artifact_id")
            if (
                not isinstance(turn_id, str)
                or not isinstance(session_id, str)
                or not isinstance(artifact_id, str)
            ):
                raise ValueError("reference identifiers required")
            turn = self.get("turns", turn_id)
            if turn is None or turn["session_id"] != session_id:
                raise ValueError("reference belongs to another session")
            if self.get("artifacts", artifact_id) is None:
                raise ValueError("reference artifact missing")

    def insert(self, table: str, data: dict[str, Any]) -> dict[str, Any]:
        data = dict(data)
        if table == "plans":
            data.setdefault("created_at", datetime.now(UTC).isoformat())
            data.setdefault(
                "expires_at",
                (datetime.fromisoformat(data["created_at"]) + timedelta(hours=24)).isoformat(),
            )
        self._validate(table, data)
        columns = self._columns(table) - {"payload"}
        structural = {key: value for key, value in data.items() if key in columns}
        structural["payload"] = json.dumps(
            {key: value for key, value in data.items() if key not in columns}, allow_nan=False
        )
        names = ",".join(f'"{key}"' for key in structural)
        placeholders = ",".join("?" for _ in structural)
        with self.transaction():
            self.connection.execute(
                f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})',
                tuple(structural.values()),
            )
            if table == "batches":
                for index, job_id in enumerate(data.get("child_job_ids", [])):
                    self.connection.execute(
                        "INSERT INTO batch_children(batch_id,job_id,position) VALUES(?,?,?)",
                        (data["id"], job_id, index),
                    )
                    self.connection.execute(
                        "UPDATE jobs SET batch_id=? WHERE id=?", (data["id"], job_id)
                    )
        return data

    def _decode(self, row: sqlite3.Row | None, include_private: bool) -> Any:
        if row is None:
            return None
        result = dict(row)
        payload = json.loads(result.pop("payload"))
        result.update(payload)
        return result if include_private else _public(result)

    def get(self, table: str, identity: str, *, include_private: bool = False) -> Any:
        columns = self._columns(table)
        if "id" not in columns:
            raise ValueError("repository requires composite identity; use list")
        row = self.connection.execute(f'SELECT * FROM "{table}" WHERE id=?', (identity,)).fetchone()
        return self._decode(row, include_private)

    def list(
        self,
        table: str,
        *,
        limit: int = 100,
        offset: int = 0,
        include_private: bool = False,
        **filters: Any,
    ) -> list[dict[str, Any]]:
        columns = self._columns(table)
        if (
            type(limit) is not int
            or not 1 <= limit <= 1000
            or type(offset) is not int
            or offset < 0
        ):
            raise ValueError("invalid pagination")
        if set(filters) - columns:
            raise ValueError("invalid filter")
        where = " AND ".join(f'"{key}"=?' for key in filters) or "1"
        rows = self.connection.execute(
            f'SELECT * FROM "{table}" WHERE {where} ORDER BY rowid LIMIT ? OFFSET ?',
            (*filters.values(), limit, offset),
        )
        return [self._decode(row, include_private) for row in rows]

    def update(self, table: str, identity: str, changes: dict[str, Any]) -> Any:
        if table in {"plans", "capabilities"}:
            raise ValueError("immutable entity; create a new record")
        if "id" in changes:
            raise ValueError("identity is immutable")
        with self.transaction():
            existing = self.get(table, identity, include_private=True)
            if existing is None:
                raise KeyError(identity)
            if table == "turns" and set(changes) - {
                "creation_state",
                "publication_state",
                "finished_at",
                "job_id",
            }:
                raise ValueError("turn attempt is immutable")
            if (
                table == "batches"
                and "child_job_ids" in changes
                and changes["child_job_ids"] != existing.get("child_job_ids", [])
            ):
                raise ValueError("batch children are immutable")
            if (
                table == "jobs"
                and "batch_id" in changes
                and changes["batch_id"] != existing.get("batch_id")
            ):
                raise ValueError("batch membership is immutable")
            if table == "approvals":
                immutable = {"plan_id", "plan_digest"}
                if existing["state"] == "consumed":
                    immutable |= {"state", "execution_job_id"}
                if any(key in changes and changes[key] != existing.get(key) for key in immutable):
                    raise ValueError("approval binding is immutable")
            if table == "jobs" and any(
                key in changes and changes[key] != existing.get(key)
                for key in ("turn_id", "plan_id")
            ):
                raise ValueError("job execution binding is immutable")
            existing.update(changes)
            self._validate(table, existing)
            columns = self._columns(table) - {"id", "payload"}
            values = {key: existing[key] for key in columns if key in existing}
            values["payload"] = json.dumps(
                {key: value for key, value in existing.items() if key not in columns | {"id"}},
                allow_nan=False,
            )
            assignments = ",".join(f'"{key}"=?' for key in values)
            self.connection.execute(
                f'UPDATE "{table}" SET {assignments} WHERE id=?', (*values.values(), identity)
            )
            return existing

    def delete(self, table: str, identity: str) -> None:
        if table not in FIELDS:
            raise ValueError("unknown entity field")
        with self.transaction():
            self.connection.execute(f'DELETE FROM "{table}" WHERE id=?', (identity,))

    def publish_turn(self, session_id: str, turn_id: str, expected_version: int) -> bool:
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("invalid expected version")
        with self.transaction():
            turn = self.get("turns", turn_id)
            if (
                not turn
                or turn["session_id"] != session_id
                or turn["creation_state"] != "succeeded"
            ):
                raise ValueError("only complete successful session turn can be published")
            cursor = self.connection.execute(
                "UPDATE sessions SET current_turn_id=?,version=version+1 "
                "WHERE id=? AND version=? AND deleted_at IS NULL",
                (turn_id, session_id, expected_version),
            )
            success = cursor.rowcount == 1
            self.update(
                "turns", turn_id, {"publication_state": "published" if success else "conflict"}
            )
            return success

    def consume_approval(
        self, approval_id: str, digest: str, turn: dict[str, Any], job: dict[str, Any]
    ) -> Any:
        with self.transaction():
            approval = self.get("approvals", approval_id, include_private=True)
            if approval is None or approval.get("plan_digest") != digest:
                raise ValueError("invalid approval")
            plan = self.get("plans", approval["plan_id"], include_private=True)
            if plan is None or plan.get("plan_digest") != digest:
                raise ValueError("invalid approval plan")
            if approval.get("state") == "consumed":
                return approval["execution_job_id"]
            if approval.get("state") != "approved":
                raise ValueError("approval is not executable")
            now = datetime.now(UTC)
            for record in (approval, plan):
                if record.get("expires_at"):
                    expires = datetime.fromisoformat(record["expires_at"])
                    if expires.tzinfo is None or expires <= now:
                        raise ValueError("approval or plan expired")
            session_id = plan.get("session_id")
            session = self.get("sessions", session_id) if session_id else None
            if (
                session is None
                or session.get("deleted_at") is not None
                or turn.get("session_id") != session_id
            ):
                raise ValueError("approval session mismatch")
            if turn.get("parent_turn_id") != plan.get("parent_turn_id"):
                raise ValueError("approval parent mismatch")
            if "expected_session_version" in plan and (
                turn.get("expected_session_version") != plan["expected_session_version"]
                or session["version"] != plan["expected_session_version"]
            ):
                raise ValueError("approval session version mismatch")
            job = dict(job)
            job.setdefault("plan_id", plan["id"])
            if job.get("turn_id") != turn.get("id") or job["plan_id"] != plan["id"]:
                raise ValueError("approval plan binding mismatch")
            self.insert("turns", turn)
            self.insert("jobs", job)
            self.update(
                "approvals", approval_id, {"state": "consumed", "execution_job_id": job["id"]}
            )
            return job["id"]

    def append_event(
        self, job_id: str, event_type: str, safe_payload: dict[str, Any]
    ) -> dict[str, Any]:
        validate_manifest(safe_payload)
        with self.transaction():
            seq = self.connection.execute(
                "SELECT COALESCE(MAX(job_seq),0)+1 FROM events WHERE job_id=?", (job_id,)
            ).fetchone()[0]
            event = {
                "id": "evt_" + uuid4().hex,
                "job_id": job_id,
                "job_seq": seq,
                "event_type": event_type,
                "timestamp": datetime.now(UTC).isoformat(),
                "safe_payload": safe_payload,
            }
            self.insert("events", event)
            return event
