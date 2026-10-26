"""Compare the installed version with the latest published GitHub release."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from typing import Any

RELEASES_URL = "https://api.github.com/repos/NekoCCN/ImagenCli/releases/latest"
REDIRECT_URL = "https://github.com/NekoCCN/ImagenCli/releases/latest"
USER_AGENT = "imagen-cli"


def installed_version() -> str:
    try:
        return version("imagen-cli")
    except PackageNotFoundError:
        return "unknown"


def _parts(value: str) -> tuple[int, ...]:
    raw = value.strip().lstrip("vV").split("-")[0].split("+")[0]
    return tuple(int(part) for part in raw.split(".") if part.isdigit())


def _tag_from_release_url(url: str) -> str | None:
    """`.../releases/tag/v0.1.0` -> `v0.1.0`; anything else is not a release page."""
    if "/releases/tag/" not in url:
        return None
    tag = url.rstrip("/").rsplit("/", 1)[-1]
    return tag or None


def _default_fetch(url: str) -> bytes:
    request = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - fixed URL
            return bytes(response.read())
    except urllib.error.HTTPError:
        # Anonymous API calls are rate limited; the release page redirect names the tag.
        page = urllib.request.Request(REDIRECT_URL, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(page, timeout=10) as response:  # noqa: S310 - fixed URL
            final = response.geturl()
        tag = _tag_from_release_url(final)
        if tag is None:
            raise
        return json.dumps({"tag_name": tag, "html_url": final}).encode()


def check(fetch: Callable[[str], bytes] | None = None) -> tuple[str, dict[str, Any]]:
    """Read the newest release tag; a network failure is reported, never guessed."""
    installed = installed_version()
    try:
        payload = json.loads((fetch or _default_fetch)(RELEASES_URL))
        latest = str(payload["tag_name"]).strip()
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError(
            f"could not read the release feed ({type(error).__name__}); "
            "check the network or open the releases page manually"
        ) from error
    latest_version = latest.lstrip("vV")
    return "succeeded", {
        "installed": installed,
        "latest": latest_version,
        "update_available": _parts(latest_version) > _parts(installed),
        "published_at": str(payload.get("published_at") or ""),
    }
