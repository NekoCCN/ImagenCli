"""Local profile commands; explicit TOML is input, never a credential source."""

import re
import sqlite3
from typing import Any

from imagen_cli.storage import queries
from imagen_cli.storage.repositories import Repositories


def read_config(path: str, identity: str) -> dict[str, Any]:
    from imagen_cli.storage.config import load_profile

    return load_profile(path, identity)


def read_key(key_file: str) -> str:
    """One secret line from a bounded local file; the value is never logged."""
    from pathlib import Path

    path = Path(key_file)
    if not path.is_file():
        raise ValueError("key file is missing")
    if path.stat().st_size > 65536:
        raise ValueError("key file is too large")
    value = path.read_bytes().decode("utf-8").strip("\r\n")
    if not value or any(char in value for char in "\r\n\0"):
        raise ValueError("key file must contain one secret line")
    return value


def secret(
    repositories: Repositories, private_store: Any, identity: str, key_file: str,
) -> tuple[str, dict[str, Any]]:
    """Store a key from a local file; the value never appears in output."""
    if private_store is None:
        raise ValueError("private credential storage is unavailable")
    value = read_key(key_file)
    row = repositories.get("profiles", identity)
    if row is None:
        matches = [item for item in queries.iter_all(repositories, "profiles")
                   if item.get("name") == identity]
        if len(matches) != 1:
            return "not_found", {}
        row = matches[0]
    reference = private_store.write_credential(str(row["id"]), value)
    updated = repositories.update(
        "profiles", str(row["id"]),
        {"credential_ref": reference, "revision": int(row["revision"]) + 1},
    )
    return "succeeded", {"profile": {
        key: value for key, value in updated.items() if key != "credential_ref"
    }}


def build_set(parameters: dict[str, Any]) -> dict[str, Any]:
    from imagen_cli.application.profile_service import ProfileService

    profile = ProfileService().build(
        identity=parameters["profile"],
        provider=parameters["provider"],
        family=parameters["family"],
        base_url=parameters["base_url"],
        models_path=parameters.get("models_path", "/v1/models"),
        model_default=parameters["model_default"],
        credential_ref=parameters["credential_ref"],
        revision=parameters.get("revision", 1),
    )
    data = profile.model_dump(mode="json")
    if "models_path" not in parameters:
        data.pop("models_path")
    return data


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
            data.setdefault(
                "models_path", existing.get("models_path") if existing else "/v1/models"
            )
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
                endpoint_changed = any(
                    data.get(key) != existing.get(key)
                    for key in ("base_url", "models_path", "family", "provider")
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
            if any(
                data.get(key) != existing.get(key)
                for key in ("base_url", "models_path", "family", "provider")
            ):
                queries.invalidate_models(repositories, profile)
            row = repositories.update("profiles", profile, {
                **{key: value for key, value in data.items() if key != "id"},
                "revision": existing["revision"] + 1,
            })
        if row is None:
            return "not_found", {}
        return "succeeded", {"profile": {
            key: value for key, value in row.items() if key != "credential_ref"
        }}
