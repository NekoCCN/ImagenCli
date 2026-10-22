"""Bundled agent skills install into project or global skill directories."""

import io
import json

from imagen_cli.application import skills_service
from imagen_cli.cli.entrypoint import main


def _run(*args: str) -> tuple[int, dict]:
    out = io.StringIO()
    code = main([*args, "--agent", "--no-interactive"], stdout=out, stderr=io.StringIO())
    return code, json.loads(out.getvalue())


def test_bundled_skills_are_discoverable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    status, data = skills_service.list_()
    assert status == "succeeded"
    names = {row["name"] for row in data["skills"]}
    assert {"imagen", "imagen-batch", "imagen-assets"} <= names
    assert all(row["description"] for row in data["skills"])
    assert not any(any(row["installed"].values()) for row in data["skills"])
    for folder in skills_service.BUNDLED.iterdir():
        text = (folder / "SKILL.md").read_text(encoding="utf-8")
        assert f"name: {folder.name}" in text


def test_install_chooses_scope_and_skills(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    status, data = skills_service.install_(["imagen"], scope="project")
    assert status == "succeeded"
    project = tmp_path / ".claude" / "skills" / "imagen" / "SKILL.md"
    assert project.is_file() and "name: imagen" in project.read_text(encoding="utf-8")
    assert not (tmp_path / ".claude" / "skills" / "imagen-batch").exists()
    assert data["destination"] == str(tmp_path / ".claude" / "skills")

    status, data = skills_service.install_(["imagen-batch"], scope="global")
    assert status == "succeeded"
    global_file = tmp_path / "home" / ".claude" / "skills" / "imagen-batch" / "SKILL.md"
    assert global_file.is_file()


def test_existing_skill_needs_force(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    skills_service.install_(["imagen"], scope="project")
    target = tmp_path / ".claude" / "skills" / "imagen" / "SKILL.md"
    target.write_text("stale", encoding="utf-8")

    status, data = skills_service.install_(["imagen"], scope="project")
    assert status == "conflict" and data["existing"][0]["name"] == "imagen"
    assert target.read_text(encoding="utf-8") == "stale"

    status, _ = skills_service.install_(["imagen"], scope="project", force=True)
    assert status == "succeeded"
    assert "name: imagen" in target.read_text(encoding="utf-8")


def test_unknown_skill_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    status, data = skills_service.install_(["nope"], scope="project")
    assert status == "not_found"
    assert "imagen" in data["available"]
    assert not (tmp_path / ".claude").exists()


def test_cli_install_and_list_are_wired(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    code, response = _run("skills", "list")
    assert code == 0 and response["ok"] is True
    assert len(response["data"]["skills"]) >= 3

    code, response = _run("skills", "install", "--skill", "imagen")
    assert code == 0 and response["ok"] is True
    assert response["data"]["installed"][0]["name"] == "imagen"
    assert (tmp_path / ".claude" / "skills" / "imagen" / "SKILL.md").is_file()

    code, response = _run("skills", "install", "--skill", "imagen")
    assert code == 5 and response["status"] == "conflict"

    code, response = _run("skills", "install", "--skill", "imagen", "--force")
    assert code == 0 and response["status"] == "succeeded"


def test_project_scope_is_the_current_folder(tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    monkeypatch.chdir(first)
    assert skills_service.install_(["imagen"])[0] == "succeeded"
    monkeypatch.chdir(second)
    assert skills_service.install_(["imagen"])[0] == "succeeded"
    assert (first / ".claude/skills/imagen/SKILL.md").is_file()
    assert (second / ".claude/skills/imagen/SKILL.md").is_file()
