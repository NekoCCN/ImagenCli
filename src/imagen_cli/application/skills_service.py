"""Install the bundled agent skills into a project or user skill directory."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

BUNDLED = Path(__file__).resolve().parent.parent / "bundled_skills"
SCOPES = ("project", "global")


def _metadata(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    block, _, _ = text.removeprefix("---\n").partition("\n---\n")
    values: dict[str, str] = {}
    for line in block.splitlines():
        key, separator, raw = line.partition(":")
        if separator:
            values[key.strip()] = raw.strip().strip('"')
    return values


def available() -> list[dict[str, str]]:
    skills: list[dict[str, str]] = []
    for folder in sorted(BUNDLED.iterdir()):
        file = folder / "SKILL.md"
        if not file.is_file():
            continue
        meta = _metadata(file.read_text(encoding="utf-8"))
        skills.append({
            "name": meta.get("name", folder.name),
            "description": meta.get("description", ""),
            "path": str(folder),
        })
    return skills


def root_for(scope: str) -> Path:
    if scope == "global":
        return Path.home() / ".claude" / "skills"
    if scope == "project":
        return Path.cwd() / ".claude" / "skills"
    raise ValueError("scope must be project or global")


def _installed(scope: str, name: str) -> bool:
    return (root_for(scope) / name / "SKILL.md").is_file()


def list_() -> tuple[str, dict[str, Any]]:
    rows = []
    for entry in available():
        rows.append({
            "name": entry["name"],
            "description": entry["description"],
            "installed": {scope: _installed(scope, entry["name"]) for scope in SCOPES},
        })
    return "succeeded", {"skills": rows}


def install_(
    names: list[str] | None, *, scope: str = "project", force: bool = False,
) -> tuple[str, dict[str, Any]]:
    root = root_for(scope)
    bundled = {entry["name"]: entry for entry in available()}
    selected = sorted(set(names or bundled))
    unknown = [name for name in selected if name not in bundled]
    if unknown:
        return "not_found", {
            "scope": scope, "destination": str(root), "installed": [], "existing": [],
            "available": sorted(bundled),
        }
    existing = [
        {"name": name, "path": str(root / name)}
        for name in selected if (root / name).exists()
    ]
    if existing and not force:
        return "conflict", {
            "scope": scope, "destination": str(root), "installed": [], "existing": existing,
        }
    root.mkdir(parents=True, exist_ok=True)
    installed = []
    for name in selected:
        target = root / name
        shutil.copytree(bundled[name]["path"], target, dirs_exist_ok=True)
        installed.append({"name": name, "path": str(target)})
    return "succeeded", {
        "scope": scope, "destination": str(root), "installed": installed, "existing": [],
    }
