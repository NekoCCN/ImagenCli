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
    operation: str | None = None,
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
    table = "profiles" if command == "providers" else "capabilities"
    items = []
    offset = 0
    while True:
        rows = repositories.list(table, limit=1000, offset=offset)
        for row in rows:
            if selected and (
                (table == "profiles" and row["id"] != selected["id"])
                or (table == "capabilities" and any(
                    row[key] != selected[key] for key in ("provider", "family")
                ))
            ):
                continue
            if model and row.get("model", row.get("model_default")) != model:
                continue
            if operation and row.get("operation") != operation:
                continue
            if command == "providers":
                item = {key: row[key] for key in ("provider", "family")}
                item["profile"] = row["id"]
            elif command == "models":
                item = {key: row[key] for key in (
                    "provider", "family", "model", "model_revision",
                )}
                if item in items:
                    continue
            else:
                item = row
            items.append(item)
        if len(rows) < 1000:
            break
        offset += len(rows)
    start = int(cursor.removeprefix("offset_")) if cursor else 0
    return "succeeded", {
        "items": items[start:start + limit],
        "next_cursor": f"offset_{start + limit}" if start + limit < len(items) else None,
        "evidence": "offline_persisted",
    }
