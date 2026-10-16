"""Local profile commands; explicit TOML is input, never a credential source."""

import re
import sqlite3
from typing import Any

from imagen_cli.storage import queries
from imagen_cli.storage.repositories import Repositories


def read_config(path: str, identity: str) -> dict[str, Any]:
    from imagen_cli.storage.config import load_profile

    return load_profile(path, identity)


def build_set(parameters: dict[str, Any]) -> dict[str, Any]:
    from imagen_cli.application.profile_service import ProfileService

    profile = ProfileService().build(
        identity=parameters["profile"],
        provider=parameters["provider"],
        family=parameters["family"],
        base_url=parameters["base_url"],
        model_default=parameters["model_default"],
        credential_ref=parameters["credential_ref"],
        revision=parameters.get("revision", 1),
    )
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
        elif command == "profile set":
            if data is None:
                raise ValueError("profile configuration required")
            if existing is None:
                if data["revision"] != 1:
                    raise ValueError("new profile revision must be one")
                try:
                    repositories.insert("profiles", data)
                except sqlite3.IntegrityError:
                    return "conflict", {}
                row = data
            else:
                if data["revision"] != existing["revision"]:
                    return "conflict", {}
                endpoint_changed = (
                    data.get("base_url") != existing.get("base_url")
                    or data.get("family") != existing.get("family")
                )
                row = repositories.update("profiles", profile, {
                    **{key: value for key, value in data.items() if key != "id"},
                    "revision": existing["revision"] + 1,
                })
                if endpoint_changed:
                    queries.invalidate_models(repositories, profile)
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
