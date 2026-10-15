"""Local profile commands; explicit TOML is input, never a credential source."""

import re
import sqlite3
import tomllib
from typing import Any

from imagen_cli.application.profile_service import ProfileService
from imagen_cli.cli.input_readers import read_text
from imagen_cli.storage.repositories import Repositories


def read_config(path: str, identity: str) -> dict[str, Any]:
    data = tomllib.loads(read_text(path))
    profile = ProfileService().load(data)
    if profile.id != identity:
        raise ValueError("profile identity mismatch")
    return profile.model_dump(mode="json")


def execute(
    repositories: Repositories, command: str, *, profile: str | None = None,
    data: dict[str, Any] | None = None, limit: int = 100, cursor: str | None = None,
) -> tuple[str, dict[str, Any]]:
    if command == "profile list":
        if cursor is not None and re.fullmatch(r"offset_[0-9]{1,12}", cursor) is None:
            raise ValueError("invalid cursor")
        offset = int(cursor.removeprefix("offset_")) if cursor else 0
        rows = repositories.list("profiles", limit=limit, offset=offset)
        more = repositories.list("profiles", limit=1, offset=offset + len(rows))
        return "succeeded", {
            "profiles": [{key: value for key, value in row.items() if key != "credential_ref"}
                         for row in rows],
            "next_cursor": f"offset_{offset + len(rows)}" if more else None,
        }
    if profile is None:
        raise ValueError("profile identity required")
    with repositories.transaction():
        existing = repositories.get("profiles", profile)
        if command == "profile show":
            row = existing
        elif command == "profile add":
            if existing:
                return "conflict", {}
            if data is None:
                raise ValueError("profile configuration required")
            if data["revision"] != 1:
                raise ValueError("new profile revision must be one")
            try:
                row = repositories.insert("profiles", data)
            except sqlite3.IntegrityError:
                return "conflict", {}
            row = data
        else:
            if existing is None:
                return "not_found", {}
            if data is None or data["revision"] != existing["revision"]:
                return "conflict", {}
            row = repositories.update("profiles", profile, {
                **{key: value for key, value in data.items() if key != "id"},
                "revision": existing["revision"] + 1,
            })
        if row is None:
            return "not_found", {}
        return "succeeded", {"profile": {
            key: value for key, value in row.items() if key != "credential_ref"
        }}
