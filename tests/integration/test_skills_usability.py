"""One target/scope contract from discovery through maintenance."""

import io
import json

import pytest

from imagen_cli.application import skill_targets
from imagen_cli.cli import entrypoint, selection


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEN_LANG", "en")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "home/.config"))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return tmp_path


def run(*args):
    out = io.StringIO()
    code = entrypoint.main([*args, "--agent"], stdout=out, stderr=io.StringIO())
    return code, json.loads(out.getvalue())


@pytest.mark.parametrize("scope", ["project", "global"])
def test_install_status_update_repeat_and_conflict(isolated, scope):
    flags = ["imagen", "--target", "claude-code", "--scope", scope]
    assert run("skills", "install", *flags)[0] == 0
    code, result = run("skills", "list", *flags)
    assert code == 0
    assert len(result["data"]["skills"]) == 1
    assert result["data"]["skills"][0]["installed"][scope]["claude-code"]
    for _ in range(2):
        code, result = run("skills", "update", *flags)
        assert code == 0
        assert result["data"]["current"] == ["imagen"]
    target = skill_targets.root_for("claude-code", scope) / "imagen/SKILL.md"
    target.write_text("local edit")
    assert run("skills", "update", *flags)[1]["status"] == "conflict"
    assert target.read_text() == "local edit"
    assert run("skills", "install", *flags)[1]["status"] == "conflict"
    assert target.read_text() == "local edit"
    assert run("skills", "uninstall", *flags)[1]["status"] == "conflict"
    assert run("skills", "update", *flags, "--force")[0] == 0
    assert run("skills", "uninstall", *flags)[0] == 0
    assert run("skills", "uninstall", *flags)[0] == 0
    code, result = run("skills", "update", *flags)
    assert code == 0
    assert result["data"]["missing"][0]["path"] == str(target.parent)


@pytest.mark.parametrize("action", ["install", "update", "uninstall"])
def test_all_mutations_use_scope_aware_picker(isolated, monkeypatch, action):
    monkeypatch.setattr(entrypoint, "_interactive", lambda *args: True)
    seen = []

    def choose(labels, **kwargs):
        seen.extend(labels)
        return ["claude-code"]

    monkeypatch.setattr(selection, "choose", choose)
    out = io.StringIO()
    code = entrypoint.main(
        ["skills", action, "imagen", "--global"], stdout=out, stderr=io.StringIO()
    )
    assert code == 0
    assert "claude-code" in seen
    assert "eve" not in seen


def test_unknown_skill_and_conflicting_input_are_actionable(isolated):
    assert run("skills", "list", "unknown")[1]["status"] == "not_found"
    code, result = run("skills", "install", "imagen", "--skill", "imagen-assets")
    assert code != 0
    assert "not both" in result["error"]["message"]


def test_maintenance_results_match_discovery_schema(isolated):
    from jsonschema import validate

    from imagen_cli.cli.registry import discover

    for action, name in [("list", "unknown"), ("update", "unknown"), ("update", "imagen")]:
        _, result = run("skills", action, name, "--target", "universal")
        validate(result["data"], discover("skills " + action)["data_schema"])


@pytest.mark.parametrize('flags', [
    ['--no-interactive'], ['--agent'], ['--output', 'json'], ['--output=jsonl'],
])
def test_unattended_modes_never_open_picker(isolated, monkeypatch, flags):
    class Terminal(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(entrypoint.sys, 'stdin', Terminal())

    def forbid_picker(*args, **kwargs):
        raise AssertionError('unattended command prompted')

    monkeypatch.setattr(selection, 'choose', forbid_picker)
    out = Terminal()
    code = entrypoint.main(['skills', 'update', 'imagen', *flags],
                           stdout=out, stderr=io.StringIO())
    assert code == 0
    assert out.getvalue()


@pytest.mark.parametrize('args', [
    ['--scope', 'global', 'skills', 'update'],
    ['skills', '--scope', 'global', 'update'],
    ['--workspace', 'state', 'skills', 'update', '--global'],
])
def test_picker_uses_parsed_command_regardless_of_option_order(isolated, monkeypatch, args):
    monkeypatch.setattr(entrypoint, '_interactive', lambda *args: True)
    calls = []

    def choose(labels, **kwargs):
        calls.append(labels)
        return ['claude-code']

    monkeypatch.setattr(selection, 'choose', choose)
    out = io.StringIO()
    assert entrypoint.main(args, stdout=out, stderr=io.StringIO()) == 0
    assert len(calls) == 1
    assert 'eve' not in calls[0]
