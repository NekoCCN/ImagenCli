import asyncio

from textual.app import App
from textual.widgets import Input, Select


def test_profile_change_resets_model_and_offers_manual_entry():
    from imagen_cli.cli.tui.forms import CreationForm

    class Harness(App):
        def on_mount(self):
            self.push_screen(
                CreationForm(
                    "Create",
                    [("profile", "", "a"), ("model", "", "a-custom")],
                    profiles=[
                        {"id": "a", "model_default": "a-default"},
                        {"id": "b", "model_default": "b-default"},
                    ],
                    models={"a": ["a-model"], "b": ["b-model"]},
                    optional={"profile", "model"},
                )
            )

    async def run():
        app = Harness()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen.query_one("#profile", Select).value = "b"
            await pilot.pause()
            assert app.screen.query_one("#model", Input).value == "b-default"
            app.screen.query_one("#model-choice", Select).value = "b-model"
            await pilot.pause()
            assert app.screen.values()["model"] == "b-model"
            app.screen.query_one("#model", Input).value = "manual"
            assert app.screen.values()["model"] == "manual"

    asyncio.run(run())
