"""Real Textual controls for maintenance and recoverable form errors."""

import asyncio

import pytest
from textual.widgets import Input, Select, SelectionList, Static

from imagen_cli.application.container import Container
from imagen_cli.cli.tui.app import Deps, ResultScreen, WorkbenchApp
from imagen_cli.cli.tui.forms import SkillsForm


async def until(pilot, condition):
    """Wait for dispatched screen callbacks, not just one idle message queue."""
    async with asyncio.timeout(5):
        while True:
            await pilot.pause()
            if condition():
                return


@pytest.fixture(autouse=True)
def isolated_agent_homes(tmp_path, monkeypatch):
    """Keep inherited CI/desktop agent configuration out of filesystem checks."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "home/.config"))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)


@pytest.mark.parametrize("defer_menu", [False, True])
@pytest.mark.parametrize("scope", ["project", "global"])
@pytest.mark.parametrize("size", [(80, 24), (140, 40)])
def test_skills_menu_scope_and_empty_selection(tmp_path, monkeypatch, size, scope, defer_menu):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CODEX_HOME", raising=False)

    async def exercise():
        with Container(workspace=str(tmp_path / "workspace")) as container:
            app = WorkbenchApp(
                Deps(container.repositories, container.artifact_store, execute=container.execute)
            )
            async with app.run_test(size=size) as pilot:
                if defer_menu:
                    skills_action = app.action_skills
                    monkeypatch.setattr(app, "action_skills",
                                        lambda: app.set_timer(0.2, skills_action))
                app.action_more()
                await until(pilot, lambda: bool(app.screen.query("#more-skills")))
                app.screen.query_one("#more-skills").press()
                await until(pilot, lambda: bool(app.screen.query("#more-install")))
                app.screen.query_one("#more-install").press()
                await until(pilot, lambda: isinstance(app.screen, SkillsForm)
                            and bool(app.screen.query("#scope")))
                screen = app.screen
                assert isinstance(screen, SkillsForm)
                screen.query_one("#scope", Select).value = "global"
                await pilot.pause()
                targets = screen.query_one("#target", SelectionList)
                assert "eve" not in [option.value for option in targets.options]
                assert str(tmp_path / "home") in str(targets.options[0].prompt)
                skills = screen.query_one("#skill", SelectionList)
                skills.deselect_all()
                await pilot.press("ctrl+enter")
                await pilot.pause()
                assert app.screen is screen
                assert "skill" in str(screen.query_one("#form-status", Static).render())
                skills.select("imagen")
                screen.query_one("#scope", Select).value = Select.NULL
                await pilot.pause()
                await pilot.press("ctrl+enter")
                assert app.screen is screen
                screen.query_one("#scope", Select).value = scope
                await pilot.pause()
                targets.deselect_all()
                targets.select("claude-code")
                await pilot.press("ctrl+enter")
                await until(pilot, lambda: isinstance(app.screen, ResultScreen))
                root = tmp_path / "home" if scope == "global" else tmp_path
                assert (root / ".claude/skills/imagen/SKILL.md").is_file()
                await pilot.press("escape")
                for action in ("list", "update", "update"):
                    app._open_command("skills " + action)
                    await until(pilot, lambda: isinstance(app.screen, SkillsForm)
                                and bool(app.screen.query("#scope")))
                    form = app.screen
                    form.query_one("#scope", Select).value = scope
                    await pilot.pause()
                    form.query_one("#skill", SelectionList).deselect_all()
                    form.query_one("#skill", SelectionList).select("imagen")
                    form.query_one("#target", SelectionList).deselect_all()
                    form.query_one("#target", SelectionList).select("claude-code")
                    await pilot.press("ctrl+enter")
                    await until(pilot, lambda: isinstance(app.screen, ResultScreen))
                    assert app.screen is not form
                    assert "imagen" in str(app.screen.body.render())
                    await pilot.press("escape")
                app._open_command("skills update")
                await until(pilot, lambda: isinstance(app.screen, SkillsForm))
                await pilot.press("escape")
                await until(pilot, lambda: not isinstance(app.screen, SkillsForm))
                assert not isinstance(app.screen, SkillsForm)

    asyncio.run(exercise())


def test_invalid_command_form_retains_values(tmp_path):
    async def exercise():
        with Container(workspace=str(tmp_path / "workspace")) as container:
            app = WorkbenchApp(
                Deps(container.repositories, container.artifact_store, execute=container.execute)
            )
            async with app.run_test() as pilot:
                app._open_command("providers")
                await until(pilot, lambda: bool(app.screen.query("#limit")))
                screen = app.screen
                screen.query_one("#limit", Input).value = "oops"
                await pilot.press("ctrl+enter")
                await pilot.pause()
                assert app.screen is screen
                assert screen.query_one("#limit", Input).value == "oops"
                screen.query_one("#limit", Input).value = "3"
                await pilot.press("ctrl+enter")
                await until(pilot, lambda: isinstance(app.screen, ResultScreen))
                assert app.screen is not screen

    asyncio.run(exercise())


def test_scope_change_never_substitutes_another_target(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))

    async def exercise():
        with Container(workspace=str(tmp_path / 'workspace')) as container:
            app = WorkbenchApp(Deps(container.repositories, container.artifact_store,
                                    execute=container.execute))
            async with app.run_test() as pilot:
                app._open_command('skills uninstall')
                await until(pilot, lambda: isinstance(app.screen, SkillsForm)
                            and bool(app.screen.query('#target')))
                form = app.screen
                targets = form.query_one('#target', SelectionList)
                targets.deselect_all()
                targets.select('eve')  # project-only
                form.query_one('#scope', Select).value = 'global'
                await pilot.pause()
                assert targets.selected == []
                await pilot.press('ctrl+enter')
                assert app.screen is form
                assert 'target' in str(form.query_one('#form-status', Static).render())
                form.query_one('#scope', Select).value = 'project'
                await pilot.pause()
                assert targets.selected == []

    asyncio.run(exercise())


def test_force_uninstall_requires_confirmation(tmp_path, monkeypatch):
    from imagen_cli.application import skills_service
    from imagen_cli.cli.tui.app import ConfirmScreen

    monkeypatch.chdir(tmp_path)
    skills_service.install_(['imagen'])
    target = tmp_path / '.agents/skills/imagen/SKILL.md'
    target.write_text('keep edits')

    async def exercise():
        with Container(workspace=str(tmp_path / 'workspace')) as container:
            app = WorkbenchApp(Deps(container.repositories, container.artifact_store,
                                    execute=container.execute))
            async with app.run_test() as pilot:
                for accept in (False, True):
                    app._open_command('skills')
                    await until(pilot, lambda: bool(app.screen.query('#more-uninstall')))
                    app.screen.query_one('#more-uninstall').press()
                    await until(pilot, lambda: isinstance(app.screen, SkillsForm)
                                and bool(app.screen.query('#scope')))
                    form = app.screen
                    form.query_one('#target', SelectionList).deselect_all()
                    form.query_one('#target', SelectionList).select('universal')
                    form.query_one('#skill', SelectionList).deselect_all()
                    form.query_one('#skill', SelectionList).select('imagen')
                    form.query_one('#force', Select).value = 'true'
                    await pilot.press('ctrl+enter')
                    await until(pilot, lambda: isinstance(app.screen, ConfirmScreen))
                    assert isinstance(app.screen, ConfirmScreen)
                    assert target.read_text() == 'keep edits'
                    await pilot.press("y" if accept else "enter")
                    if accept:
                        await until(pilot, lambda: isinstance(app.screen, ResultScreen))
                    else:
                        await until(pilot, lambda: not isinstance(app.screen, ConfirmScreen))
                    assert target.exists() is not accept

    asyncio.run(exercise())


@pytest.mark.parametrize("timeout", ["-1", "0", "bad", "86401"])
def test_invalid_shared_options_keep_form_open(tmp_path, timeout):
    async def exercise():
        with Container(workspace=str(tmp_path / 'workspace')) as container:
            app = WorkbenchApp(Deps(container.repositories, container.artifact_store,
                                    execute=container.execute))
            async with app.run_test() as pilot:
                app._open_command('job wait')
                await until(pilot, lambda: bool(app.screen.query('#job_id')))
                form = app.screen
                form.query_one('#job_id', Input).value = 'job_' + 'a' * 32
                form.query_one('#timeout', Input).value = timeout
                await pilot.press('ctrl+enter')
                await pilot.pause()
                assert app.screen is form
                assert form.query_one('#timeout', Input).value == timeout

    asyncio.run(exercise())
