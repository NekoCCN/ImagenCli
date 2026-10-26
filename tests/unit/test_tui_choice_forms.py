"""Choice-based forms exercise real widgets without credentials or network calls."""

import asyncio

from textual.app import App
from textual.widgets import Input, Select

from imagen_cli.cli.tui.forms import ChoiceForm


def test_choices_submit_values_and_keep_manual_text():
    class Harness(App):
        result = None

        def on_mount(self):
            self.push_screen(
                ChoiceForm(
                    "Test",
                    [("kind", "", "one"), ("model", "", "custom")],
                    choices={"kind": [("First", "one"), ("Second", "two")]},
                ),
                self.finished,
            )

        def finished(self, result):
            self.result = result

    async def run():
        app = Harness()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen.query_one("#kind", Select).value = "two"
            assert app.screen.query_one("#model", Input).value == "custom"
            await pilot.click("#submit")
            await pilot.pause()
            assert app.result == {"kind": "two", "model": "custom"}

    asyncio.run(run())
