"""Provider forms expose explicit credential modes without storing draft secrets."""

import asyncio

from textual.app import App
from textual.widgets import Input, Select


def test_provider_credentials_and_family_follow_choices():
    from imagen_cli.cli.tui.forms import ProviderForm

    class Harness(App):
        def on_mount(self):
            self.push_screen(ProviderForm("Provider", [], fetch_models=lambda values: []))

    async def run():
        app = Harness()
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            screen.query_one("#provider", Select).value = "gemini"
            await pilot.pause()
            assert screen.values()["family"] == "generate_content"
            assert "googleapis.com" in screen.values()["base_url"]
            screen.query_one("#credential_mode", Select).value = "env"
            await pilot.pause()
            screen.query_one("#credential_ref", Input).value = "MY_KEY"
            assert screen.values()["credential_ref"] == "env:MY_KEY"
            assert "api_key" not in screen.values()

    asyncio.run(run())


def test_fetch_draft_models_fills_choice_without_saving():
    from imagen_cli.cli.tui.forms import ProviderForm

    requests = []

    def fetch(values):
        requests.append(values)
        return ["test-model"]

    class Harness(App):
        def on_mount(self):
            self.push_screen(ProviderForm("Provider", [], fetch_models=fetch))

    async def run():
        app = Harness()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.query_one("#api_key", Input).value = "test-only"
            screen.query_one("#fetch-models").press()
            await app.workers.wait_for_complete()
            await pilot.pause()
            screen.query_one("#model-choice", Select).value = "test-model"
            await pilot.pause()
            assert screen.values()["model_default"] == "test-model"
            assert requests[0]["api_key"] == "test-only"
            assert app.screen is screen
            screen.action_cancel()
            await pilot.pause()
            assert app.screen is not screen

    asyncio.run(run())
