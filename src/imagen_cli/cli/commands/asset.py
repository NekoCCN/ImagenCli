"""Read-only artifact facts for humans and agents."""

from typing import Any

from imagen_cli.storage import queries
from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.repositories import Repositories


def show_(
    repositories: Repositories, store: ArtifactStore, artifact: str,
) -> tuple[str, dict[str, Any]]:
    row = repositories.get("artifacts", artifact)
    if row is None:
        return "not_found", {}
    path = store.get(artifact)
    owners: list[dict[str, Any]] = []
    for ref in queries.iter_all(repositories, "artifact_references", artifact_id=artifact):
        owners.append({
            "session_id": ref["session_id"], "turn_id": ref["turn_id"],
            "role": ref["role"], "index": ref["index"], "source": "reference",
        })
    for link in queries.iter_all(repositories, "artifact_links", artifact_id=artifact):
        owners.append({
            "session_id": link["session_id"], "role": link["role"], "source": "link",
        })
    turn = (
        repositories.get("turns", row["producer_turn_id"])
        if row.get("producer_turn_id") else None
    )
    job = (
        repositories.get("jobs", row["producer_job_id"])
        if row.get("producer_job_id") else None
    )
    return "succeeded", {
        "artifact": row,
        "path": str(path) if path is not None else None,
        "available": path is not None,
        "owners": owners,
        "producer_turn": turn,
        "producer_job": job,
        "next_actions": [
            {"command": f"asset open {artifact}", "creates_paid_request": False},
        ],
    }


def delete_(
    repositories: Repositories, store: ArtifactStore, artifact: str,
) -> tuple[str, dict[str, Any]]:
    from imagen_cli.application.cleanup_service import delete_artifact

    try:
        data = delete_artifact(repositories, store, artifact)
    except KeyError:
        return "not_found", {}
    return "succeeded", data

