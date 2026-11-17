"""Real Textual controls for maintenance and recoverable form errors."""

import asyncio

import pytest
from textual.widgets import Input, Select, SelectionList, Static

from imagen_cli.application.container import Container
from imagen_cli.cli.tui.app import Deps, WorkbenchApp
from imagen_cli.cli.tui.forms import SkillsForm


@pytest.mark.parametrize("scope", ["project", "global"])
@pytest.mark.parametrize("size", [(80, 24), (140, 40)])
def test_skills_menu_scope_and_empty_selection(tmp_path, monkeypatch, size, scope):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CODEX_HOME", raising=False)

    async def exercise():
        with Container(workspace=str(tmp_path / "workspace")) as container:
            app = WorkbenchApp(
                Deps(container.repositories, container.artifact_store, execute=container.execute)
            )
            async with app.run_test(size=size) as pilot:
                app.action_more()
                await pilot.pause()
                app.screen.query_one("#more-skills").press()
                await pilot.pause()
                app.screen.query_one("#more-install").press()
                await pilot.pause()
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
                await app.workers.wait_for_complete()
                await pilot.pause()
                root = tmp_path / "home" if scope == "global" else tmp_path
                assert (root / ".claude/skills/imagen/SKILL.md").is_file()
                await pilot.press("escape")
                for action in ("list", "update", "update"):
                    app._open_command("skills " + action)
                    await pilot.pause()
                    form = app.screen
                    form.query_one("#scope", Select).value = scope
                    await pilot.pause()
                    form.query_one("#skill", SelectionList).deselect_all()
                    form.query_one("#skill", SelectionList).select("imagen")
                    form.query_one("#target", SelectionList).deselect_all()
                    form.query_one("#target", SelectionList).select("claude-code")
                    await pilot.press("ctrl+enter")
                    await app.workers.wait_for_complete()
                    await pilot.pause()
                    assert app.screen is not form
                    assert "imagen" in str(app.screen.body.render())
                    await pilot.press("escape")
                app._open_command("skills update")
                await pilot.pause()
                await pilot.press("escape")
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
                await pilot.pause()
                screen = app.screen
                screen.query_one("#limit", Input).value = "oops"
                await pilot.press("ctrl+enter")
                await pilot.pause()
                assert app.screen is screen
                assert screen.query_one("#limit", Input).value == "oops"
                screen.query_one("#limit", Input).value = "3"
                await pilot.press("ctrl+enter")
                await app.workers.wait_for_complete()
                await pilot.pause()
                assert app.screen is not screen

    asyncio.run(exercise())
