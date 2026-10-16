"""Fork a session from an explicit source turn without touching the source."""

from datetime import UTC, datetime
from typing import Any

from imagen_cli.domain.primitive import new_id
from imagen_cli.storage.repositories import Repositories


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def fork(
    repositories: Repositories, session_id: str, *, turn_id: str,
    artifact_ids: tuple[str, ...] = (), name: str | None = None,
) -> dict[str, Any]:
    with repositories.transaction():
        source = repositories.get("sessions", session_id)
        if source is None or source.get("deleted_at") is not None:
            raise KeyError(session_id)
        turn = repositories.get("turns", turn_id)
        if turn is None or turn["session_id"] != session_id:
            raise ValueError("source turn must belong to the session")
        for identity in artifact_ids:
            artifact = repositories.get("artifacts", identity)
            if artifact is None or artifact["save_state"] != "published":
                raise ValueError("selected artifact is unavailable")
            refs = repositories.list("artifact_references", turn_id=turn_id)
            if identity not in {ref["artifact_id"] for ref in refs}:
                raise ValueError("selected artifact does not belong to the turn")
        return repositories.insert("sessions", {
            "id": new_id("ses"),
            "name": name or f"{source['name']} 分支",
            "intent": source["intent"],
            "default_profile_id": source["default_profile_id"],
            "default_model": source["default_model"],
            "current_turn_id": None,
            "version": 0,
            "created_at": _now(),
            "deleted_at": None,
            "origin_session_id": session_id,
            "origin_turn_id": turn_id,
        })
