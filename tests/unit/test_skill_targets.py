import pytest

from imagen_cli.application import skill_targets


def test_targets_cover_upstream_and_resolve_scopes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
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


def test_the_default_target_is_the_shared_agents_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XSXDG", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    assert skill_targets.DEFAULT_TARGET == "universal"
    assert skill_targets.default_root("project") == tmp_path / ".agents/skills"


def test_prompt_order_leads_with_the_shared_directory_and_preselection(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / ".claude").mkdir()
    labels, preselected = skill_targets.prompt_order()
    assert labels[0] == "universal"
    assert "claude-code" in labels[:3]
    assert "universal" in preselected
    assert "claude-code" in preselected


def test_a_bare_directory_name_is_not_a_detected_agent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "skills").mkdir()
    assert "openclaw" not in skill_targets.detected()


def test_the_shared_directory_does_not_preselect_every_agent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / ".agents").mkdir()
    assert skill_targets.detected() == []


def test_agents_installed_elsewhere_are_not_detected_in_this_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home/.claude").mkdir(parents=True)
    assert "claude-code" not in skill_targets.detected()


def test_an_empty_config_home_still_resolves_to_the_user_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", "")
    assert skill_targets.root_for("opencode", "global") == (
        tmp_path / "home/.config/opencode/skills"
    )
