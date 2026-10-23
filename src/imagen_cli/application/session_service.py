"""Local session history and CAS publication; never dispatches provider work."""

import sqlite3
from datetime import UTC, datetime
from typing import Any

from imagen_cli.application.cleanup_service import released_name
from imagen_cli.domain.contracts import JsonObject
from imagen_cli.domain.enums import (
    ArtifactRole,
    CreationState,
    DispatchState,
    JobState,
    LocalSaveState,
    Operation,
    PublicationState,
    RemoteState,
)
from imagen_cli.domain.models import Artifact, ArtifactReference, Job, Session, Turn
from imagen_cli.domain.primitive import new_id
from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.repositories import Repositories


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class SessionService:
    def __init__(self, repositories: Repositories, artifact_store: ArtifactStore) -> None:
        if artifact_store.repositories.connection is not repositories.connection:
            raise ValueError("session and artifact storage must share a connection")
        self.repositories = repositories
        self.artifact_store = artifact_store

    def _session(self, identity: str) -> Session:
        row = self.repositories.get("sessions", identity)
        if row is None:
            raise KeyError(identity)
        session = Session.model_validate(row)
        if session.deleted_at is not None:
            raise ValueError("session is deleted")
        return session

    def _turn(self, identity: str) -> Turn:
        row = self.repositories.get("turns", identity)
        if row is None:
            raise KeyError(identity)
        return Turn.model_validate(row)

    def _rows(self, table: str, **filters: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        while True:
            page = self.repositories.list(table, limit=1000, offset=len(rows), **filters)
            rows.extend(page)
            if len(page) < 1000:
                return rows

    def create_session(
        self,
        name: str,
        *,
        intent: str = "",
        default_profile_id: str | None = None,
        default_model: str | None = None,
    ) -> Session:
        session = Session(
            id=new_id("ses"),
            name=name,
            intent=intent,
            default_profile_id=default_profile_id,
            default_model=default_model,
            version=0,
            created_at=_now(),
        )
        try:
            self.repositories.insert("sessions", session.model_dump(mode="json"))
        except sqlite3.IntegrityError:
            # Rows soft-deleted before delete freed the name still hold it; free it and retry.
            holder = next((
                row for row in self.repositories.list("sessions", name=name)
                if row.get("deleted_at") is not None
            ), None)
            if holder is None:
                raise
            self.repositories.update("sessions", holder["id"], {"name": released_name(holder)})
            self.repositories.insert("sessions", session.model_dump(mode="json"))
        return session

    def create_turn(
        self,
        session_id: str,
        *,
        instruction: str,
        operation: str,
        resolved_profile_snapshot: JsonObject,
        model: str,
        parameters: JsonObject,
        requested_count: int = 1,
        parent_turn_id: str | None = None,
        references: dict[str, list[str]] | None = None,
    ) -> Turn:
        with self.repositories.transaction():
            session = self._session(session_id)
            now = _now()
            turn = Turn(
                id=new_id("trn"),
                session_id=session.id,
                parent_turn_id=parent_turn_id,
                instruction=instruction,
                operation=Operation(operation),
                resolved_profile_snapshot=resolved_profile_snapshot,
                model=model,
                parameters=parameters,
                input_manifest=[],
                expected_session_version=session.version,
                job_id=new_id("job"),
                creation_state=CreationState.QUEUED,
                publication_state=PublicationState.NOT_ATTEMPTED,
                created_at=now,
            )
            if parent_turn_id is not None:
                turn.validate_parent(self._turn(parent_turn_id))
            job = Job(
                id=turn.job_id,
                turn_id=turn.id,
                state=JobState.QUEUED,
                dispatch_state=DispatchState.NOT_STARTED,
                remote_state=RemoteState.NOT_SUBMITTED,
                local_save_state=LocalSaveState.NOT_STARTED,
                requested_count=requested_count,
                actual_count=0,
                accepted_maybe=False,
                created_at=now,
                updated_at=now,
            )
            refs = []
            for role, identities in (references or {}).items():
                if role not in {"input", "mask", "reference"}:
                    raise ValueError("only input, mask and reference may be selected")
                for index, identity in enumerate(identities):
                    ref = ArtifactReference(
                        session_id=session.id,
                        turn_id=turn.id,
                        artifact_id=identity,
                        role=ArtifactRole(role),
                        index=index,
                    )
                    if self.artifact_store.get(identity) is None:
                        raise ValueError("input artifact must be published")
                    refs.append(ref)
            turn.input_manifest = [
                {"artifact_id": ref.artifact_id, "role": ref.role.value, "index": ref.index}
                for ref in refs
            ]
            # The schema has a circular turn/job FK; bind the job after both inserts.
            self.repositories.insert(
                "turns",
                {**turn.model_dump(mode="json"), "job_id": None},
            )
            self.repositories.insert("jobs", job.model_dump(mode="json"))
            self.repositories.update("turns", turn.id, {"job_id": job.id})
            for ref in refs:
                self.repositories.insert("artifact_references", ref.model_dump(mode="json"))
            return turn

    def save_artifact(
        self,
        turn_id: str,
        data: bytes,
        *,
        origin_manifest: JsonObject | None = None,
    ) -> Artifact:
        turn = self._turn(turn_id)
        self._session(turn.session_id)
        if turn.publication_state != "not_attempted":
            raise ValueError("cannot add outputs to a published or conflicting attempt")
        identity = new_id("art")
        # Keep staging facts durable on failure so ArtifactStore.reconcile can recover.
        self.artifact_store.stage(
            identity,
            data,
            producer_turn_id=turn.id,
            producer_job_id=turn.job_id,
            origin_manifest=origin_manifest,
        )
        self.artifact_store.publish(identity)
        with self.repositories.transaction():
            self.repositories.update("artifacts", identity, {"created_at": _now()})
            artifact = Artifact.model_validate(self.repositories.get("artifacts", identity))
            outputs = self._rows("artifact_references", turn_id=turn.id, role="output")
            reference = ArtifactReference(
                session_id=turn.session_id,
                turn_id=turn.id,
                artifact_id=identity,
                role=ArtifactRole.OUTPUT,
                index=len(outputs),
            )
            self.repositories.insert("artifact_references", reference.model_dump(mode="json"))
            return artifact

    def publish_turn(self, turn_id: str) -> bool:
        with self.repositories.transaction():
            turn = self._turn(turn_id)
            self._session(turn.session_id)
            # Re-observation is not another CAS attempt, even after a later edit.
            if turn.publication_state != "not_attempted":
                return turn.publication_state == "published"
            job = self.repositories.get("jobs", turn.job_id)
            if (
                turn.creation_state != "succeeded"
                or job is None
                or job["turn_id"] != turn.id
                or job["state"] != "succeeded"
                or job["remote_state"] != "succeeded"
                or job["local_save_state"] != "saved"
            ):
                raise ValueError("only a completely saved successful attempt can be published")
            outputs = self._rows("artifact_references", turn_id=turn.id, role="output")
            if (
                not outputs
                or len(outputs) != job["actual_count"]
                or len(outputs) < job["requested_count"]
                or len({ref["artifact_id"] for ref in outputs}) != len(outputs)
            ):
                raise ValueError("complete successful outputs are required")
            for ref in outputs:
                artifact = self.repositories.get("artifacts", ref["artifact_id"])
                if (
                    artifact is None
                    or artifact["producer_turn_id"] != turn.id
                    or artifact["producer_job_id"] != turn.job_id
                    or self.artifact_store.get(ref["artifact_id"]) is None
                ):
                    raise ValueError("output must be a saved artifact from this attempt")
            return self.repositories.publish_turn(
                turn.session_id,
                turn.id,
                turn.expected_session_version,
            )

    def resume(self, session_id: str) -> dict[str, Any]:
        """Read one consistent history snapshot without creating or retrying work."""
        with self.repositories.transaction():
            session = self._session(session_id)
            turns = [Turn.model_validate(row) for row in self._rows("turns", session_id=session.id)]
            references = [
                ArtifactReference.model_validate(row)
                for row in self._rows("artifact_references", session_id=session.id)
            ]
            if session.current_turn_id is not None:
                session.validate_current_turn(self._turn(session.current_turn_id))
            return {"session": session, "turns": turns, "references": references}
