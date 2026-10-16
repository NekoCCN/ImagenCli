"""Declared model catalogs from a trusted endpoint; never capability evidence."""

from __future__ import annotations

import ipaddress
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx

from imagen_cli.storage.repositories import Repositories

TIMEOUT = 30.0
TTL = timedelta(hours=24)


def _trusted(base_url: str) -> bool:
    parsed = urlsplit(base_url)
    if parsed.scheme == "https" and parsed.hostname:
        return True
    if parsed.scheme != "http" or not parsed.hostname:
        return False
    if parsed.hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


def _headers(family: str, credential: str | None) -> dict[str, str]:
    if credential is None:
        return {}
    if family == "generate_content":
        return {"x-goog-api-key": credential}
    return {"Authorization": f"Bearer {credential}"}


def _identities(family: str, payload: Any) -> list[dict[str, str]]:
    if family == "generate_content":
        items = payload.get("models") if isinstance(payload, dict) else None
        result = []
        for item in items if isinstance(items, list) else []:
            name = item.get("name") if isinstance(item, dict) else None
            if isinstance(name, str) and name:
                model_id = name.removeprefix("models/")
                display = str(item.get("displayName") or model_id)
            result.append({"model_id": model_id, "display_name": display})
        return result
    items = payload.get("data") if isinstance(payload, dict) else None
    result = []
    for item in items if isinstance(items, list) else []:
        identity = item.get("id") if isinstance(item, dict) else None
        if isinstance(identity, str) and identity:
            result.append({"model_id": identity, "display_name": identity})
    return result


def refresh(
    repositories: Repositories,
    profile: dict[str, Any],
    *,
    resolve_credential: Any = None,
    send_credential: bool = False,
    client: httpx.Client | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    base_url = str(profile["base_url"])
    if not _trusted(base_url):
        raise ValueError("model catalog requires https or a loopback http endpoint")
    credential = None
    reference = profile.get("credential_ref")
    if send_credential and isinstance(reference, str):
        if resolve_credential is None:
            raise ValueError("credential sender unavailable")
        credential = resolve_credential(reference)
    url = base_url.rstrip("/") + "/models"
    owned = client is None
    http = client or httpx.Client(
        timeout=TIMEOUT, follow_redirects=False, trust_env=False,
    )
    try:
        response = http.get(url, headers=_headers(str(profile["family"]), credential))
    except httpx.TimeoutException as error:
        raise ValueError("catalog request timed out") from error
    except httpx.HTTPError as error:
        raise ValueError("catalog request failed") from error
    finally:
        if owned:
            http.close()
    if response.status_code in {401, 403}:
        raise ValueError("catalog authentication failed")
    if response.status_code == 429:
        raise ValueError("catalog rate limited")
    if 500 <= response.status_code < 600:
        raise ValueError("catalog endpoint failed")
    if response.status_code == 404:
        raise ValueError("catalog endpoint not found")
    if response.status_code != 200:
        raise ValueError("catalog request failed")
    try:
        payload = response.json()
    except ValueError as error:
        raise ValueError("catalog response is not JSON") from error
    stamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat().replace("+00:00", "Z")
    stored = []
    with repositories.transaction():
        for item in _identities(str(profile["family"]), payload):
            identity = f"{profile['id']}:{item['model_id']}"
            row = {
                "id": identity,
                "profile_id": profile["id"],
                "model_id": item["model_id"],
                "display_name": item["display_name"],
                "fetched_at": stamp,
                "source_url": url,
                "evidence": "discovered",
            }
            if repositories.get("provider_models", identity) is None:
                repositories.insert("provider_models", row)
            else:
                repositories.update("provider_models", identity, {
                    key: value for key, value in row.items() if key != "id"
                })
            stored.append(row)
    return {"profile": profile["id"], "source_url": url, "fetched_at": stamp, "models": stored}


def add(repositories: Repositories, profile: dict[str, Any], model_id: str) -> dict[str, Any]:
    identity = f"{profile['id']}:{model_id}"
    stamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    row = {
        "id": identity, "profile_id": profile["id"], "model_id": model_id,
        "display_name": model_id, "fetched_at": stamp, "source_url": "",
        "evidence": "manual",
    }
    if repositories.get("provider_models", identity) is not None:
        raise ValueError("model already declared")
    repositories.insert("provider_models", row)
    return row


def listing(repositories: Repositories, profile: dict[str, Any]) -> list[dict[str, Any]]:
    rows = repositories.list("provider_models", limit=1000, profile_id=profile["id"])
    now = datetime.now(UTC)
    for row in rows:
        fetched = datetime.fromisoformat(row["fetched_at"].replace("Z", "+00:00"))
        row["stale"] = now - fetched > TTL
    return rows
