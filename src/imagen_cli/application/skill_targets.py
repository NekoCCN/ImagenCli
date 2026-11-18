"""Agent directory snapshot from vercel-labs/skills docs/agents.md (MIT, 2026-10-25)."""

from __future__ import annotations

import json
import os
from pathlib import Path


def targets() -> list[dict[str, str]]:
    return list(json.loads(Path(__file__).with_suffix(".json").read_text(encoding="utf-8")))


DEFAULT_TARGET = "universal"


def default_root(scope: str) -> Path:
    return root_for(DEFAULT_TARGET, scope)


def prompt_order(scope: str = "project") -> tuple[list[str], list[str]]:
    """Picker labels (shared directory first, then detected agents) and what starts ticked."""
    found = set(detected(scope)) - {DEFAULT_TARGET}
    names = [row["name"] for row in targets() if row["name"] != DEFAULT_TARGET and row.get(scope)]
    first = [name for name in names if name in found]
    rest = [name for name in names if name not in found]
    return [DEFAULT_TARGET, *first, *rest], [DEFAULT_TARGET, *first]


def root_for(target: str, scope: str) -> Path:
    if scope not in {"project", "global"}:
        raise ValueError("scope must be project or global")
    row = next((row for row in targets() if row["name"] == target), None)
    if row is None:
        raise ValueError(f"unknown agent target: {target}")
    value = row[scope]
    if not value:
        raise ValueError(f"{target} does not support {scope} skills")
    if scope == "project":
        return Path.cwd() / value
    if target == "codex" and os.environ.get("CODEX_HOME"):
        return Path(os.environ["CODEX_HOME"]).expanduser() / "skills"
    if target == "claude-code" and os.environ.get("CLAUDE_CONFIG_DIR"):
        return Path(os.environ["CLAUDE_CONFIG_DIR"]).expanduser() / "skills"
    if value.startswith("~/.config/"):
        config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        return config / value.removeprefix("~/.config/")
    return Path(value).expanduser()


def detected(scope: str = "project") -> list[str]:
    """Agents whose directory exists in the chosen scope; the shared one is implicit."""
    shared = root_for(DEFAULT_TARGET, scope)
    found = []
    for row in targets():
        if not row.get(scope):
            continue
        candidate = root_for(row["name"], scope)
        if candidate == shared or candidate.parent == Path.cwd():
            continue
        if candidate.parent.is_dir():
            found.append(row["name"])
    return found
