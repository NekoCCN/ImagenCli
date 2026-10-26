"""Install the bundled agent skills into a project or user skill directory."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from importlib.metadata import version
from pathlib import Path
from typing import Any

from imagen_cli.application import skill_targets

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
        skills.append(
            {
                "name": meta.get("name", folder.name),
                "version": meta.get("version", "0.0.0"),
                "description": meta.get("description", ""),
                "path": str(folder),
            }
        )
    return skills


def root_for(scope: str) -> Path:
    if scope == "global":
        return Path.home() / ".claude" / "skills"
    if scope == "project":
        return Path.cwd() / ".claude" / "skills"
    raise ValueError("scope must be project or global")


def _roots(
    scope: str, agents: list[str] | None, *, deduplicate: bool = True
) -> list[tuple[str, Path]]:
    names = agents or [skill_targets.DEFAULT_TARGET]
    if "all" in names:
        names = [row["name"] for row in skill_targets.targets() if row.get(scope)]
    roots = []
    seen: set[Path] = set()
    for agent in names:
        root = skill_targets.root_for(agent, scope)
        if root in seen and deduplicate:
            continue
        seen.add(root)
        roots.append((agent, root))
    return roots


def _safe(path: Path) -> bool:
    return not any(item.is_symlink() for item in (path, *path.parents)) and (
        not path.is_dir() or not any(item.is_symlink() for item in path.rglob("*"))
    )


def _managed(folder: Path, name: str) -> dict[str, Any] | None:
    if not _safe(folder):
        return None
    try:
        value = json.loads((folder / ".imagen-install.json").read_text(encoding="utf-8"))
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != 1
            or value.get("skill") != name
            or not isinstance(value.get("files"), dict)
            or 'SKILL.md' not in value.get('files', {})
            or not isinstance(value.get('package_version'), str)
            or not value.get('package_version')
            or any(
                not isinstance(path, str)
                or Path(path).is_absolute()
                or ".." in Path(path).parts
                or not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in '0123456789abcdef' for character in digest)
                for path, digest in value.get("files", {}).items()
            )
        ):
            return None
        return value
    except (OSError, ValueError):
        return None


def _manifest(folder: Path, name: str) -> dict[str, Any]:
    files = {}
    for file in sorted(folder.rglob("*")):
        if file.is_file() and file != folder / ".imagen-install.json":
            files[str(file.relative_to(folder))] = hashlib.sha256(file.read_bytes()).hexdigest()
    version_file = folder / "SKILL.md"
    skill_version = (
        _metadata(version_file.read_text(encoding="utf-8")).get("version", "0.0.0")
        if version_file.is_file()
        else "0.0.0"
    )
    return {
        "schema_version": 1,
        "skill": name,
        "skill_version": skill_version,
        "package_version": version("imagen-cli"),
        "files": files,
    }


def _installed(scope: str, name: str, agents: list[str] | None = None) -> bool:
    return any((root / name / "SKILL.md").is_file() for _, root in _roots(scope, agents))


def _state(root: Path, name: str, bundled: dict[str, Any]) -> dict[str, Any]:
    """What one installed copy is: present, which version, and whether it drifted."""
    target = root / name
    if not (target / "SKILL.md").is_file():
        return {"installed": False, "version": None, "update_available": False}
    record = _managed(target, name)
    if record is None:
        return {"installed": True, "version": None, "update_available": False}
    installed_version = str(record.get("skill_version") or "0.0.0")
    differs = record.get("files") != bundled["files"]
    return {
        "installed": True,
        "version": installed_version,
        "update_available": installed_version != bundled["version"] or differs,
    }


def list_(
    *, agents: list[str] | None = None, scope: str | None = None
) -> tuple[str, dict[str, Any]]:
    rows = []
    scopes = (scope,) if scope else SCOPES
    for entry in available():
        bundled = {
            **entry,
            "files": _manifest(Path(entry["path"]), entry["name"])["files"],
        }
        states = {
            current: {
                agent: _state(root, entry["name"], bundled)
                for agent, root in _roots(current, agents or ["all"], deduplicate=False)
            }
            for current in scopes
        }
        installed: dict[str, Any]
        versions: dict[str, Any]
        updates: dict[str, Any]
        if agents is None:
            installed = {c: any(v["installed"] for v in states[c].values()) for c in scopes}
            versions = {
                c: next((v["version"] for v in states[c].values() if v["installed"]), None)
                for c in scopes
            }
            updates = {c: any(v["update_available"] for v in states[c].values()) for c in scopes}
        else:
            installed = {
                c: {a: value["installed"] for a, value in states[c].items()} for c in scopes
            }
            versions = {c: {a: value["version"] for a, value in states[c].items()} for c in scopes}
            updates = {
                c: {a: value["update_available"] for a, value in states[c].items()}
                for c in scopes
            }
        rows.append(
            {
                "name": entry["name"],
                "version": entry["version"],
                "description": entry["description"],
                "installed": installed,
                "installed_version": versions,
                "update_available": updates,
            }
        )
    return "succeeded", {"skills": rows}


def install_(
    names: list[str] | None,
    *,
    scope: str = "project",
    force: bool = False,
    agents: list[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    bundled = {entry["name"]: entry for entry in available()}
    selected = sorted(set(names or bundled))
    roots = _roots(scope, agents)
    unknown = [name for name in selected if name not in bundled]
    if unknown:
        return "not_found", {
            "scope": scope,
            "destination": str(roots[0][1]),
            "installed": [],
            "existing": [],
            "available": sorted(bundled),
        }
    existing = []
    for agent, root in roots:
        for name in selected:
            target = root / name
            managed = _managed(target, name) if target.exists() else None
            if not _safe(target):
                existing.append({"name": name, "path": str(target), "agent": agent})
            elif target.exists() and managed is None:
                existing.append({"name": name, "path": str(target), "agent": agent})
            elif target.exists() and not force:
                existing.append({"name": name, "path": str(target), "agent": agent})
    if existing:
        return "conflict", {
            "scope": scope,
            "destination": str(roots[0][1]),
            "installed": [],
            "existing": existing,
        }
    installed = []
    failed = []
    for agent, root in roots:
        for name in selected:
            target = root / name
            recovery: str | None = None
            temporary: Path | None = None
            try:
                root.mkdir(parents=True, exist_ok=True)
                temporary = Path(tempfile.mkdtemp(dir=root, prefix='.imagen-install-'))
                staging = temporary / name
                shutil.copytree(bundled[name]['path'], staging)
                (staging / '.imagen-install.json').write_text(
                    json.dumps(_manifest(staging, name), indent=2) + '\n', encoding='utf-8'
                )
                backup = temporary / 'previous'
                if target.exists():
                    target.rename(backup)
                try:
                    staging.rename(target)
                except OSError:
                    if backup.exists():
                        recovery = str(backup)
                        backup.rename(target)
                        recovery = None
                    raise
                installed.append({'name': name, 'path': str(target), 'agent': agent})
            except OSError:
                failure = {'name': name, 'path': str(target), 'agent': agent}
                if recovery:
                    failure['backup'] = recovery
                failed.append(failure)
            finally:
                if temporary is not None and recovery is None:
                    shutil.rmtree(temporary, ignore_errors=True)
    expanded = (
        [row["name"] for row in skill_targets.targets() if row.get(scope)]
        if agents and "all" in agents
        else agents or [skill_targets.DEFAULT_TARGET]
    )
    return ("internal_error" if failed else "succeeded"), {
        "scope": scope,
        "destination": str(roots[0][1]),
        "installed": installed,
        "existing": [],
        "failed": failed,
        "targets": expanded,
        "unsupported": [row["name"] for row in skill_targets.targets() if not row.get(scope)]
        if agents and "all" in agents
        else [],
    }


def update_(
    names: list[str] | None,
    *,
    scope: str = "project",
    force: bool = False,
    agents: list[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Reinstall installed skills whose bundled copy changed; never clobber local edits."""
    bundled = {entry["name"]: entry for entry in available()}
    selected = sorted(set(names or bundled))
    unknown = [name for name in selected if name not in bundled]
    if unknown:
        return "not_found", {
            "scope": scope,
            "updated": [],
            "current": [],
            "conflicts": [],
            "available": sorted(bundled),
        }
    changed: dict[str, list[str]] = {}
    current: list[dict[str, str]] = []
    conflicts: list[dict[str, str]] = []
    for agent, root in _roots(scope, agents):
        for name in selected:
            target = root / name
            if not (target / "SKILL.md").is_file():
                continue
            record = _managed(target, name)
            source = Path(bundled[name]["path"])
            if record is None:
                # A copy Imagen did not write: identical is fine, anything else is not ours.
                if _manifest(target, name)["files"] == _manifest(source, name)["files"]:
                    current.append({"name": name, "path": str(target), "agent": agent})
                else:
                    conflicts.append(
                        {
                            "name": name,
                            "path": str(target),
                            "agent": agent,
                            "reason": "unmanaged",
                        }
                    )
                continue
            recorded = record.get("files")
            dirty = _manifest(target, name)["files"] != recorded
            if dirty and not force:
                conflicts.append(
                    {
                        "name": name,
                        "path": str(target),
                        "agent": agent,
                        "reason": "modified",
                    }
                )
                continue
            if (
                not dirty
                and record.get("skill_version") == bundled[name]["version"]
                and recorded == _manifest(source, name)["files"]
            ):
                current.append({"name": name, "path": str(target), "agent": agent})
                continue
            changed.setdefault(agent, []).append(name)
    if conflicts:
        return "conflict", {
            "scope": scope,
            "updated": [],
            "current": [row["name"] for row in current],
            "conflicts": conflicts,
        }
    installed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for agent, agent_names in changed.items():
        status, data = install_(agent_names, scope=scope, force=True, agents=[agent])
        installed.extend(data.get("installed", []))
        failed.extend(data.get("failed", []))
        if status == "internal_error" and not failed:
            failed.append({"agent": agent, "names": agent_names})
    return ("internal_error" if failed else "succeeded"), {
        "scope": scope,
        "updated": sorted({str(row["name"]) for row in installed}),
        "current": sorted({row["name"] for row in current}),
        "conflicts": [],
        "installed": installed,
        "failed": failed,
    }


def uninstall_(
    names: list[str] | None,
    *,
    scope: str = "project",
    force: bool = False,
    yes: bool = False,
    agents: list[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    selected = sorted(set(names or {entry["name"] for entry in available()}))
    if set(selected) - {entry["name"] for entry in available()}:
        return "not_found", {"scope": scope, "removed": [], "conflicts": []}
    conflicts = []
    targets = []
    for agent, root in _roots(scope, agents):
        for name in selected:
            target = root / name
            if not _safe(target):
                conflicts.append({"name": name, "path": str(target), "agent": agent})
                continue
            if not target.exists():
                continue
            record = _managed(target, name)
            if record is None:
                conflicts.append({"name": name, "path": str(target), "agent": agent})
                continue
            expected = record["files"]
            actual = _manifest(target, name)["files"]
            if expected != actual and not (force and yes):
                conflicts.append({"name": name, "path": str(target), "agent": agent})
            else:
                targets.append((agent, name, target))
    if conflicts:
        return "conflict", {"scope": scope, "removed": [], "conflicts": conflicts}
    removed = []
    failed = []
    mappings = _roots(scope, agents, deduplicate=False)
    for agent, name, target in targets:
        record = {
            'name': name, 'path': str(target), 'agent': agent,
            'agents': [owner for owner, root in mappings if root / name == target],
        }
        try:
            shutil.rmtree(target)
            removed.append(record)
        except OSError:
            failed.append(record)
    return ('internal_error' if failed else 'succeeded'), {
        'scope': scope, 'removed': removed, 'conflicts': [], 'failed': failed,
    }
