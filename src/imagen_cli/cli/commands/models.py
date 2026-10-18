"""Provider model catalog commands; the only network call is an explicit refresh."""

from __future__ import annotations

from typing import Any, cast

from imagen_cli.application import model_catalog_service
from imagen_cli.storage import queries
from imagen_cli.storage.repositories import Repositories


def _profile(repositories: Repositories, identity: str) -> dict[str, Any]:
    row = repositories.get("profiles", identity)
    if row is not None:
        return cast(dict[str, Any], row)
    matches = [
        item for item in queries.iter_all(repositories, "profiles")
        if item.get("name") == identity
    ]
    if len(matches) != 1:
        raise KeyError(identity)
    return matches[0]


def refresh_(
    repositories: Repositories,
    identity: str,
    *,
    send_credential: bool,
    resolve_credential: Any,
) -> tuple[str, dict[str, Any]]:
    profile = _profile(repositories, identity)
    data = model_catalog_service.refresh(
        repositories, profile,
        resolve_credential=resolve_credential, send_credential=send_credential,
    )
    return "succeeded", data


def add_(repositories: Repositories, identity: str, model_id: str) -> tuple[str, dict[str, Any]]:
    profile = _profile(repositories, identity)
    try:
        row = model_catalog_service.add(repositories, profile, model_id)
    except ValueError:
        return "conflict", {}
    return "succeeded", {"model": row}


def use_(repositories: Repositories, identity: str, model_id: str) -> tuple[str, dict[str, Any]]:
    """Set the profile default; an undeclared model is registered as manual."""
    profile = _profile(repositories, identity)
    if repositories.get("provider_models", f"{profile['id']}:{model_id}") is None:
        model_catalog_service.add(repositories, profile, model_id)
    if profile["model_default"] == model_id:
        return "succeeded", {"profile": _public_profile(profile)}
    with repositories.transaction():
        updated = repositories.update("profiles", str(profile["id"]), {
            "model_default": model_id, "revision": int(profile["revision"]) + 1,
        })
    return "succeeded", {"profile": _public_profile(updated)}


def _public_profile(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "credential_ref"}


def list_(repositories: Repositories, identity: str) -> tuple[str, dict[str, Any]]:
    profile = _profile(repositories, identity)
    return "succeeded", {"models": model_catalog_service.listing(repositories, profile)}
