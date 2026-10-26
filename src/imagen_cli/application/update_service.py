"""Compare the installed version with the latest published GitHub release."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

RELEASES_URL = "https://api.github.com/repos/NekoCCN/ImagenCli/releases/latest"
ATOM_URL = "https://github.com/NekoCCN/ImagenCli/releases.atom"
USER_AGENT = "imagen-cli"
STANDARD_REQUIREMENT = "imagen-cli[tui,postprocess]"
UPGRADE_COMMANDS = {
    "uv": ["uv", "tool", "upgrade", "imagen-cli"],
    "pipx": ["pipx", "upgrade", "imagen-cli"],
}


def install_method() -> str:
    """Which standard installer owns this interpreter."""
    executable = str(Path(sys.executable)).replace("\\", "/")
    if "/uv/tools/" in executable:
        return "uv"
    if "/pipx/venvs/" in executable:
        return "pipx"
    return "unknown"


def installed_version() -> str:
    try:
        return version("imagen-cli")
    except PackageNotFoundError:
        return "unknown"


def _parts(value: str) -> tuple[int, ...]:
    raw = value.strip().lstrip("vV").split("-")[0].split("+")[0]
    return tuple(int(part) for part in raw.split(".") if part.isdigit())


def _tag_from_atom(text: str) -> str | None:
    """The newest release tag in the public Atom feed, newest first."""
    match = re.search(r"/releases/tag/([^\"<>\s]+)", text)
    return match.group(1) if match else None


def _default_fetch(url: str) -> bytes:
    request = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - fixed URL
            return bytes(response.read())
    except urllib.error.HTTPError:
        # Anonymous API calls are rate limited; the release feed is not.
        feed = urllib.request.Request(ATOM_URL, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(feed, timeout=10) as response:  # noqa: S310 - fixed URL
            tag = _tag_from_atom(response.read().decode("utf-8", "replace"))
        if tag is None:
            raise
        return json.dumps({"tag_name": tag}).encode()


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


def _run(command: list[str]) -> tuple[int, str]:
    result = subprocess.run(command, capture_output=True, text=True, timeout=900, check=False)
    tail = (result.stderr or result.stdout or "").strip().splitlines()[-1:] or [""]
    return result.returncode, tail[0]


def upgrade(
    *,
    fetch: Callable[[str], bytes] | None = None,
    run: Callable[[list[str]], tuple[int, str]] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Install the newer release with the standard installer that owns this copy."""
    status, data = check(fetch)
    method = install_method()
    if not data["update_available"]:
        return "succeeded", {**data, "upgraded": False, "method": method}
    command = UPGRADE_COMMANDS.get(method)
    if command is None:
        raise ValueError(
            "not installed with uv tool or pipx; upgrade with your installer, "
            f"or reinstall: uv tool install \"{STANDARD_REQUIREMENT}\""
        )
    code, detail = (run or _run)(command)
    if code != 0:
        raise ValueError(f"upgrade command failed ({code}): {detail}")
    return "succeeded", {
        **data,
        "upgraded": True,
        "method": method,
        "command": " ".join(command),
    }
