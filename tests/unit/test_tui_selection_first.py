"""Presets are primary; custom values remain editable without being lost."""

import asyncio

from textual.app import App
from textual.widgets import Input, Select

from imagen_cli.cli.tui.forms import CreationForm, ProviderForm


def test_provider_presets_hide_inputs_until_custom_selected():
    class Harness(App):
        def on_mount(self):
            self.push_screen(ProviderForm("Provider", [], fetch_models=lambda _: ["listed"]))

    async def run():
        app = Harness()
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            assert not screen.query_one("#base_url", Input).display
            assert not screen.query_one("#models_path", Input).display
            screen.query_one("#endpoint-choice", Select).value = "custom"
            await pilot.pause()
            assert screen.query_one("#base_url", Input).display
            screen.query_one("#base_url", Input).value = "https://custom.example/v1beta"
            screen.query_one("#provider", Select).value = "gemini"
            await pilot.pause()
            assert screen.values()["base_url"] == "https://custom.example/v1beta"
            screen.query_one("#models-path-choice", Select).value = "custom"
            await pilot.pause()
            assert screen.query_one("#models_path", Input).display
            screen.query_one("#models_path", Input).value = "/custom/models"
            screen.query_one("#fetch-models").press()
            await app.workers.wait_for_complete()
            await pilot.pause()
            screen.query_one("#model-choice", Select).value = "listed"
            await pilot.pause()
            assert screen.values()["model_default"] == "listed"
            assert not screen.query_one("#model_default", Input).display
            screen.query_one("#model-choice", Select).value = "custom"
            await pilot.pause()
            assert screen.query_one("#model_default", Input).display
            assert screen.values()["model_default"] == "listed"

    asyncio.run(run())


def test_existing_custom_profile_opens_without_changing_values():
    fields = [
        ("provider", "", "gemini"),
        ("base_url", "", "https://custom.example/v1beta"),
        ("models_path", "", "/custom/models"),
        ("model_default", "", "saved-model"),
        ("credential_ref", "", "env:KEY"),
    ]

    class Harness(App):
        def on_mount(self):
            self.push_screen(ProviderForm("Edit", fields, fetch_models=lambda _: []))

    async def run():
        app = Harness()
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            assert screen.query_one("#endpoint-choice", Select).value == "custom"
            assert screen.query_one("#models-path-choice", Select).value == "custom"
            assert screen.query_one("#base_url", Input).display
            assert screen.query_one("#models_path", Input).display
            assert screen.values()["base_url"] == "https://custom.example/v1beta"
            assert screen.values()["models_path"] == "/custom/models"
            assert screen.values()["model_default"] == "saved-model"
            assert screen.query_one("#model-choice", Select).value == "saved-model"

    asyncio.run(run())


def test_creation_defaults_to_model_choice_and_preserves_custom_value():
    class Harness(App):
        def on_mount(self):
            self.push_screen(
                CreationForm(
                    "Create",
                    [("profile", "", "a"), ("model", "", "saved")],
                    profiles=[
                        {
                            "id": "a",
                            "name": "Gateway",
                            "provider": "gemini",
                            "model_default": "saved",
                        }
                    ],
                    models={"a": ["listed"]},
                )
            )

    async def run():
        app = Harness()
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            assert screen.query_one("#model-choice", Select).value == "saved"
            assert not screen.query_one("#model", Input).display
            screen.query_one("#model-choice", Select).value = "custom"
            await pilot.pause()
            screen.query_one("#model", Input).value = "my-custom"
            assert screen.values()["model"] == "my-custom"

    asyncio.run(run())
