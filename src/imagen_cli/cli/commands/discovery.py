"""Read-only discovery of local declarations, never a live provider probe."""

import re
from typing import Any

from imagen_cli.storage.repositories import Repositories


def list_(
    repositories: Repositories,
    command: str,
    *,
    profile: str | None = None,
    model: str | None = None,
    limit: int = 100,
    cursor: str | None = None,
) -> tuple[str, dict[str, Any]]:
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("invalid limit")
    if cursor is not None and re.fullmatch(r"offset_[0-9]{1,12}", cursor) is None:
        raise ValueError("invalid cursor")
    selected = repositories.get("profiles", profile) if profile else None
    if profile and selected is None:
        return "not_found", {}
    items: list[dict[str, Any]] = []
    if command == "providers":
        for row in repositories.list("profiles", limit=1000):
            if selected and row["id"] != selected["id"]:
                continue
            items.append({
                "profile": row["id"], "provider": row["provider"], "family": row["family"],
            })
    else:
        for row in repositories.list("provider_models", limit=1000):
            if selected and row["profile_id"] != selected["id"]:
                continue
            if model and row["model_id"] != model:
                continue
            owner = repositories.get("profiles", row["profile_id"])
            items.append({
                "profile": row["profile_id"],
                "provider": (owner or {}).get("provider"),
                "family": (owner or {}).get("family"),
                "model": row["model_id"], "model_revision": "local",
            })
    start = int(cursor.removeprefix("offset_")) if cursor else 0
    return "succeeded", {
        "items": items[start:start + limit],
        "next_cursor": f"offset_{start + limit}" if start + limit < len(items) else None,
        "evidence": "offline_persisted",
    }
