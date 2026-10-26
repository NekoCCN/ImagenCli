"""Bundled skills carry a version, report drift, and can be updated in place."""

from __future__ import annotations

import io
import json
from pathlib import Path

from imagen_cli.application import skills_service
from imagen_cli.cli.entrypoint import main


def _workspace(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))


def _install(tmp_path: Path, monkeypatch, name: str = "imagen") -> Path:
    _workspace(tmp_path, monkeypatch)
    status, _ = skills_service.install_([name], scope="project")
    assert status == "succeeded"
    return tmp_path / ".claude" / "skills" / name


def _manifest(target: Path) -> dict:
    return json.loads((target / ".imagen-install.json").read_text(encoding="utf-8"))


def _write_manifest(target: Path, manifest: dict) -> None:
    (target / ".imagen-install.json").write_text(json.dumps(manifest), encoding="utf-8")


def _bundled_version(name: str = "imagen") -> str:
    return next(entry["version"] for entry in skills_service.available() if entry["name"] == name)


def test_every_bundled_skill_declares_a_version():
    versions = {entry["name"]: entry["version"] for entry in skills_service.available()}
    assert versions
    for name, version in versions.items():
        assert version, name


def test_list_reports_the_installed_version(tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch)
    status, data = skills_service.list_(scope="project")
    row = next(item for item in data["skills"] if item["name"] == "imagen")
    assert row["version"] == _bundled_version()
    assert row["installed_version"]["project"] == _bundled_version()
    assert row["update_available"]["project"] is False


def test_update_restores_an_outdated_install(tmp_path, monkeypatch):
    target = _install(tmp_path, monkeypatch)
    manifest = _manifest(target)
    manifest["skill_version"] = "0.0.1"
    _write_manifest(target, manifest)

    status, data = skills_service.update_(None, scope="project")
    assert status == "succeeded"
    assert "imagen" in data["updated"]
    assert _manifest(target)["skill_version"] == _bundled_version()


def test_update_refuses_a_locally_edited_skill(tmp_path, monkeypatch):
    target = _install(tmp_path, monkeypatch)
    (target / "SKILL.md").write_text("hand edited\n", encoding="utf-8")

    status, data = skills_service.update_(None, scope="project")
    assert status == "conflict"
    assert data["conflicts"][0]["name"] == "imagen"
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "hand edited\n"


def test_update_force_overwrites_local_edits(tmp_path, monkeypatch):
    target = _install(tmp_path, monkeypatch)
    (target / "SKILL.md").write_text("hand edited\n", encoding="utf-8")

    status, data = skills_service.update_(None, scope="project", force=True)
    assert status == "succeeded"
    assert "hand edited" not in (target / "SKILL.md").read_text(encoding="utf-8")


def test_the_cli_exposes_skills_update(tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch)
    out = io.StringIO()
    code = main(
        ["skills", "update", "--scope", "project", "--agent", "--no-interactive"],
        stdout=out,
        stderr=io.StringIO(),
    )
    assert code == 0
    value = json.loads(out.getvalue())
    assert value["ok"] is True
    assert value["data"]["scope"] == "project"
