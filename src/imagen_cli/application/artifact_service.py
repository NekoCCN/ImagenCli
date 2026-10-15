"""Save existing provider output through the shared durable artifact store."""

from collections.abc import Callable
from typing import Any

from imagen_cli.domain.primitive import new_id
from imagen_cli.providers.base import ArtifactDescriptor
from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.repositories import Repositories


class ArtifactService:
    def __init__(self, repositories: Repositories, artifact_store: ArtifactStore) -> None:
        self.repositories = repositories
        self.artifact_store = artifact_store

    def _job_turn(self, job_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        job = self.repositories.get("jobs", job_id)
        if job is None:
            raise KeyError(job_id)
        turn = self.repositories.get("turns", job["turn_id"])
        if turn is None or turn.get("job_id") != job_id:
            raise ValueError("job must belong to its producer turn")
        return job, turn

    def _reference(self, row: dict[str, Any]) -> None:
        _, turn = self._job_turn(row["producer_job_id"])
        index = row["origin_manifest"]["output_index"]
        refs = self.repositories.list(
            "artifact_references",
            turn_id=turn["id"],
            role="output",
            index=index,
        )
        if refs:
            if refs[0]["artifact_id"] != row["id"]:
                raise ValueError("output slot already occupied")
            return
        self.repositories.insert(
            "artifact_references",
            {
                "session_id": turn["session_id"],
                "turn_id": turn["id"],
                "artifact_id": row["id"],
                "role": "output",
                "index": index,
            },
        )

    def save(
        self,
        job_id: str,
        data: bytes,
        *,
        artifact_id: str | None = None,
        index: int | None = None,
    ) -> dict[str, Any]:
        _, turn = self._job_turn(job_id)
        with self.repositories.transaction():
            refs = self.repositories.list("artifact_references", turn_id=turn["id"], role="output")
            if index is None:
                index = max((ref["index"] for ref in refs), default=-1) + 1
            if type(index) is not int or index < 0:
                raise ValueError("invalid output index")
            if any(ref["index"] == index for ref in refs):
                raise ValueError("output slot already occupied")
        row = self.artifact_store.stage(
            artifact_id or new_id("art"),
            data,
            producer_job_id=job_id,
            producer_turn_id=turn["id"],
            origin_manifest={"kind": "provider_output", "output_index": index},
        )
        self._reference(row)
        return dict(self.artifact_store.publish(row["id"]))

    def fetch(
        self,
        job_id: str,
        descriptor: ArtifactDescriptor,
        fetch: Callable[[ArtifactDescriptor], bytes],
    ) -> dict[str, Any]:
        # Authorization and bounded retrieval belong to the injected provider/download boundary.
        self._job_turn(job_id)
        return self.save(
            job_id,
            fetch(descriptor),
            artifact_id=descriptor.id,
            index=descriptor.index,
        )

    def reconcile(self, artifact_id: str) -> dict[str, Any]:
        row = self.repositories.get("artifacts", artifact_id)
        if row is None:
            raise KeyError(artifact_id)
        self._reference(row)
        return dict(self.artifact_store.reconcile(artifact_id))
