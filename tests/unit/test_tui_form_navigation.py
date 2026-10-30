"""Real form navigation without workspace, credentials, or provider requests."""

import asyncio

import pytest
from textual.app import App
from textual.widgets import Input, Select

from imagen_cli.cli.tui.forms import ChoiceForm, ProviderForm


@pytest.fixture(autouse=True)
def isolated_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


class Harness(App):
    def __init__(self, form):
        super().__init__()
        self.form = form
        self.results = []

    def on_mount(self):
        self.push_screen(self.form, self.results.append)


@pytest.mark.parametrize("default", ["listed", ""])
def test_initial_focus_is_visible_custom_selector(default):
    async def run():
        form = ChoiceForm(
            "Choose",
            [("model", "", default)],
            choices={"model": [("Listed", "listed")]},
            custom={"model"},
        )
        app = Harness(form)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert form.focused is form.query_one("#model-choice", Select)
            assert form.query_one("#model", Input).display is (not default)

    asyncio.run(run())


def test_initial_focus_skips_fields_hidden_or_disabled_after_super_mount():
    class RestrictedForm(ChoiceForm):
        def on_mount(self):
            super().on_mount()
            self.query_one("#hidden", Input).display = False
            self.query_one("#disabled", Input).disabled = True

    async def run():
        form = RestrictedForm(
            "Visible fields",
            [("hidden", "", "a"), ("disabled", "", "b"), ("visible", "", "c")],
        )
        app = Harness(form)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert form.focused is form.query_one("#visible", Input)

    asyncio.run(run())


def test_input_enter_moves_to_next_visible_control_without_submitting():
    async def run():
        form = ChoiceForm(
            "Navigate",
            [("prompt", "", "ready"), ("model", "", "listed"), ("tail", "", "end")],
            choices={"model": [("Listed", "listed")]},
            custom={"model"},
        )
        app = Harness(form)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            assert app.screen is form
            assert app.results == []
            assert form.focused is form.query_one("#model-choice", Select)
            await pilot.press("tab")
            assert form.focused is form.query_one("#tail", Input)
            await pilot.press("enter")
            assert app.screen is form
            assert form.focused is form.query_one("#submit")
            assert app.results == []

    asyncio.run(run())


def test_select_enter_only_selects_a_value():
    async def run():
        form = ChoiceForm(
            "Choose",
            [("kind", "", "one")],
            choices={"kind": [("One", "one"), ("Two", "two")]},
        )
        app = Harness(form)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter", "down", "enter")
            assert form.query_one("#kind", Select).value == "two"
            assert app.screen is form
            assert app.results == []

    asyncio.run(run())


@pytest.mark.parametrize("control", ["input", "select"])
def test_ctrl_enter_explicitly_submits_from_either_control(control):
    async def run():
        form = ChoiceForm(
            "Save",
            [("value", "", "ready")],
            choices={"value": [("Ready", "ready")]} if control == "select" else None,
        )
        app = Harness(form)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("ctrl+enter")
            assert app.results == [{"value": "ready"}]
            assert app.screen is not form

    asyncio.run(run())


@pytest.mark.parametrize("activation", ["enter", "click"])
def test_save_button_submits(activation):
    async def run():
        form = ChoiceForm("Save", [("value", "", "ready")])
        app = Harness(form)
        async with app.run_test() as pilot:
            await pilot.pause()
            if activation == "enter":
                form.query_one("#submit").focus()
                await pilot.pause()
                await pilot.press("enter")
            else:
                await pilot.click("#submit")
            assert app.results == [{"value": "ready"}]

    asyncio.run(run())


def test_provider_navigation_skips_disabled_family_and_hidden_inputs():
    async def run():
        form = ProviderForm("Provider", [], fetch_models=lambda _: [])
        app = Harness(form)
        async with app.run_test(size=(100, 48)) as pilot:
            await pilot.pause()
            assert form.focused is form.query_one("#profile", Input)
            await pilot.press("enter")
            assert form.focused is form.query_one("#provider", Select)
            await pilot.press("tab")
            assert form.focused is form.query_one("#endpoint-choice", Select)
            await pilot.press("tab")
            assert form.focused is form.query_one("#models-path-choice", Select)
            await pilot.press("tab")
            assert form.focused is form.query_one("#credential_mode", Select)
            await pilot.press("tab")
            assert form.focused is form.query_one("#api_key", Input)
            await pilot.press("enter")
            assert form.focused is form.query_one("#model-choice", Select)
            assert app.screen is form
            assert app.results == []

    asyncio.run(run())
