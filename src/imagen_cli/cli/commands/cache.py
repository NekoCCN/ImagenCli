"""Cache audit and confirmed purge; local only, no network."""

from __future__ import annotations

import os
from typing import Any

from imagen_cli.application import cache_service
from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.repositories import Repositories


def _days(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    if not raw.isdigit():
        raise ValueError(f"{name} must be a non-negative integer")
    return int(raw)


def _limits() -> tuple[int, int]:
    return (
        _days("IMAGEN_CACHE_STAGING_DAYS", 7),
        _days("IMAGEN_CACHE_DELETED_SESSION_DAYS", 30),
    )


def audit_(repositories: Repositories, store: ArtifactStore) -> dict[str, Any]:
    staging, deleted = _limits()
    return cache_service.report(
        cache_service.audit(repositories, store, staging_days=staging, deleted_days=deleted)
    )


def purge_(
    repositories: Repositories,
    store: ArtifactStore,
    *,
    plan: str | None = None,
    yes: bool = False,
) -> dict[str, Any]:
    staging, deleted = _limits()
    if plan is None or not yes:
        return {"dry_run": True, **audit_(repositories, store)}
    result = cache_service.purge(
        repositories, store, plan, staging_days=staging, deleted_days=deleted
    )
    return {"dry_run": False, "plan_digest": plan, **result}
