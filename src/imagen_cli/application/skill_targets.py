"""Agent directory snapshot from vercel-labs/skills docs/agents.md (MIT, 2026-10-25)."""

from __future__ import annotations

import json
import os
from pathlib import Path


def targets() -> list[dict[str, str]]:
    return list(json.loads(Path(__file__).with_suffix(".json").read_text(encoding="utf-8")))


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
        config = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        return config / value.removeprefix("~/.config/")
    return Path(value).expanduser()


def detected() -> list[str]:
    found = []
    for row in targets():
        if (Path.cwd() / row["project"]).parent.is_dir():
            found.append(row["name"])
        elif row["global"] and root_for(row["name"], "global").parent.is_dir():
            found.append(row["name"])
    return found
