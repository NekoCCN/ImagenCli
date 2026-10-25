import pytest

from imagen_cli.application import skill_targets


def test_targets_cover_upstream_and_resolve_scopes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    rows = skill_targets.targets()
    assert len(rows) >= 79
    assert len({row["name"] for row in rows}) == len(rows)
    assert skill_targets.root_for("claude-code", "project") == tmp_path / ".claude/skills"
    assert skill_targets.root_for("codex", "project") == tmp_path / ".agents/skills"
    assert skill_targets.root_for("codex", "global") == tmp_path / "home/.agents/skills"
    assert skill_targets.root_for("cline", "project") == skill_targets.root_for("codex", "project")
    with pytest.raises(ValueError):
        skill_targets.root_for("eve", "global")
    with pytest.raises(ValueError):
        skill_targets.root_for("unknown", "project")
    with pytest.raises(ValueError):
        skill_targets.root_for("codex", "other")


def test_config_and_tool_environment_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    assert skill_targets.root_for("opencode", "global") == tmp_path / "config/opencode/skills"
    assert skill_targets.root_for("codex", "global") == tmp_path / "codex/skills"
    assert isinstance(skill_targets.detected(), list)
