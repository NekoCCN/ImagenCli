"""Interactive setup covers skill options without changing unattended commands."""

import io

import pytest

from imagen_cli.application import skill_targets, skills_service
from imagen_cli.cli.entrypoint import main


class Terminal(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "home/.config"))
    monkeypatch.setenv("IMAGEN_LANG", "en")
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return tmp_path


def invoke(monkeypatch, argv, inputs):
    monkeypatch.setattr("sys.stdin", Terminal(inputs))
    out, err = Terminal(), io.StringIO()
    code = main(argv, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


@pytest.mark.parametrize("scope,choice", [("project", "1"), ("global", "2")])
def test_hub_and_bare_actions_choose_scope_and_subset(isolated, monkeypatch, scope, choice):
    # Action=install, scope, universal, imagen-assets only, safe review.
    code, _, err = invoke(monkeypatch, ["skills"], f"2\n{choice}\n1\n2\n1\n")
    assert code == 0
    root = skill_targets.root_for("universal", scope)
    assert sorted(p.name for p in root.iterdir()) == ["imagen-assets"]
    assert "Equivalent command: imagen skills install imagen-assets" in err
    for action in ("list", "update", "update", "uninstall", "uninstall"):
        code, _, _ = invoke(monkeypatch, ["skills", action], f"{choice}\n1\n2\n1\n")
        assert code == 0
    assert not (root / "imagen-assets").exists()


def test_back_to_scope_and_skill_selection_and_cancel(isolated, monkeypatch):
    # Scope project → back from agents → global; then review → back to skills → different subset.
    code, _, err = invoke(monkeypatch, ["skills", "install"], "1\nb\n2\n1\n1\nb\n2\n1\n")
    assert code == 0
    assert (skill_targets.root_for("universal", "global") / "imagen-assets/SKILL.md").is_file()
    assert not (isolated / ".agents").exists()
    for inputs in ("q\n", "1\nq\n", "1\n1\nq\n", "1\n1\n1\nq\n", ""):
        code, _, _ = invoke(monkeypatch, ["skills", "install"], inputs)
        assert code == 7
    assert not (isolated / ".agents").exists()


@pytest.mark.parametrize(
    "action,token", [("install", "overwrite"), ("update", "overwrite"), ("uninstall", "remove")]
)
def test_override_requires_exact_confirmation(isolated, monkeypatch, action, token):
    skills_service.install_(["imagen"])
    target = isolated / ".agents/skills/imagen/SKILL.md"
    target.write_text("local edits")
    code, _, _ = invoke(monkeypatch, ["skills", action], "1\n1\n1\n1\n")
    assert code == 5 and target.read_text() == "local edits"
    code, _, _ = invoke(monkeypatch, ["skills", action], "1\n1\n1\n2\ny\nq\n")
    assert code == 7 and target.read_text() == "local edits"
    code, _, err = invoke(monkeypatch, ["skills", action], f"1\n1\n1\n2\n{token}\n")
    assert code == 0 and "--force" in err
    if action == "uninstall":
        assert "--yes" in err and not target.exists()
    else:
        assert target.read_text() != "local edits"


def test_override_never_takes_over_unmanaged_skill(isolated, monkeypatch):
    target = isolated / ".agents/skills/imagen"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("not managed")
    code, _, _ = invoke(monkeypatch, ["skills", "install"], "1\n1\n1\n2\noverwrite\n")
    assert code == 5
    assert (target / "SKILL.md").read_text() == "not managed"


def test_launcher_and_unattended_group(isolated, monkeypatch):
    code, _, _ = invoke(monkeypatch, [], "2\n2\n1\n1\n1\n1\n")
    assert code == 0 and (isolated / ".agents/skills/imagen/SKILL.md").is_file()
    code, out, _ = invoke(monkeypatch, [], "3\n")
    assert code == 0 and "skills" in out
    code, _, _ = invoke(monkeypatch, ["skills", "--no-interactive"], "")
    assert code == 2
    out = io.StringIO()
    assert main(["skills", "update", "--agent"], stdout=out, stderr=io.StringIO()) == 0


def test_launcher_workspace_choice_has_back_and_reset(isolated):
    from imagen_cli.cli.skills_wizard import launcher

    err = io.StringIO()
    workspace = str(isolated / "image work")
    assert launcher(stdin=io.StringIO(f"4\nb\n4\n{workspace}\n1\n"), stderr=err) == [
        "tui", "--workspace", workspace,
    ]
    assert "Project Skills still use the current directory" in err.getvalue()
    assert launcher(stdin=io.StringIO(f"4\n{workspace}\n4\n\n3\n"), stderr=err) == ["help"]
    with pytest.raises(KeyboardInterrupt):
        launcher(stdin=io.StringIO("4\nq\n"), stderr=err)
