"""Local session handlers; return command status/data for the shared renderer."""

import re
import sqlite3
from typing import Any

from imagen_cli.application.session_service import SessionService


def create(
    service: SessionService,
    *,
    name: str,
    intent: str = "",
    profile: str | None = None,
    model: str | None = None,
) -> tuple[str, dict[str, Any]]:
    try:
        row = service.create_session(
            name, intent=intent, default_profile_id=profile, default_model=model
        )
    except sqlite3.IntegrityError:
        return "conflict", {}
    return "succeeded", {"session": row.model_dump(mode="json")}


def _sessions(service: SessionService) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = service.repositories.list("sessions", limit=1000, offset=offset)
        rows.extend(row for row in page if row.get("deleted_at") is None)
        if len(page) < 1000:
            return rows
        offset += len(page)


def list_(
    service: SessionService, *, limit: int = 100, cursor: str | None = None
) -> tuple[str, dict[str, Any]]:
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("invalid limit")
    if cursor is not None and re.fullmatch(r"offset_[0-9]{1,12}", cursor) is None:
        raise ValueError("invalid cursor")
    offset = int(cursor.removeprefix("offset_")) if cursor else 0
    all_rows = _sessions(service)
    rows = all_rows[offset : offset + limit]
    more = len(all_rows) > offset + limit
    return "succeeded", {
        "sessions": rows,
        "next_cursor": f"offset_{offset + limit}" if more else None,
    }


def show(service: SessionService, identity: str) -> tuple[str, dict[str, Any]]:
    repositories = service.repositories
    row = repositories.get("sessions", identity)
    if row is None:
        matches = [row for row in _sessions(service) if row["name"] == identity]
        if len(matches) > 1:
            return "conflict", {}
        row = matches[0] if matches else None
    if row is None or row.get("deleted_at") is not None:
        return "not_found", {}
    snapshot = service.resume(row["id"])
    return "succeeded", {
        "session": snapshot["session"].model_dump(mode="json"),
        "turns": [turn.model_dump(mode="json") for turn in snapshot["turns"]],
        "references": [ref.model_dump(mode="json") for ref in snapshot["references"]],
    }


def resume(service: SessionService, identity: str) -> tuple[str, dict[str, Any]]:
    status, data = show(service, identity)
    if status == "succeeded":
        data["next_actions"] = [
            {"command": f"session show {data['session']['id']}", "creates_paid_request": False}
        ]
    return status, data
