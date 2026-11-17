import asyncio

from textual.app import App
from textual.widgets import Input, Select

from imagen_cli.cli.tui.forms import ProviderForm


def test_provider_manual_novelai_never_fetches():
    calls = []

    class Harness(App):
        def on_mount(self):
            self.push_screen(ProviderForm("Profile", [], fetch_models=lambda v: calls.append(v)))

    async def run():
        async with Harness().run_test() as pilot:
            screen = pilot.app.screen
            screen.query_one("#provider", Select).value = "novelai"
            await pilot.pause()
            screen.query_one("#model_default", Input).value = "manual-model"
            assert screen.values()["family"] == "generate_image"
            assert screen.values()["auth_mode"] == "bearer"
            assert screen.values()["vibe_encoding_transport"] == "json"
            screen.query_one("#vibe_encoding_transport", Select).value = "multipart"
            assert screen.values()["vibe_encoding_transport"] == "multipart"
            assert calls == []

    asyncio.run(run())


def test_creation_parameters_roles_and_provider_reset():
    from imagen_cli.cli.tui.app import _creation_arguments
    from imagen_cli.cli.tui.forms import CreationForm

    calls = []

    class Harness(App):
        def on_mount(self):
            self.push_screen(
                CreationForm(
                    "Generate",
                    [
                        ("prompt", "", "draw"),
                        ("profile", "", "nai"),
                        ("model", "", "manual"),
                        ("count", "", "1"),
                    ],
                    profiles=[
                        {"id": "nai", "provider": "novelai", "family": "generate_image"},
                        {"id": "old", "provider": "openai", "family": "images"},
                    ],
                    models={},
                    artifacts=[("source", "art_test")],
                    vibe_action=calls.append,
                )
            )

    async def run():
        async with Harness().run_test(size=(100, 40)) as pilot:
            screen = pilot.app.screen
            screen.query_one("#nai_scale", Input).value = "5.5"
            screen.query_one("#nai_strength", Input).value = "0.5"
            screen.query_one("#nai_noise", Input).value = "0.1"
            screen.query_one("#nai_sm", Input).value = "false"
            screen.query_one("#nai_negative_prompt", Input).value = "blur"
            screen.query_one("#nai_primary", Select).value = "art_test"
            screen.query_one("#nai_reference", Select).value = "art_test"
            screen.query_one("#nai_role", Select).value = "vibe"
            await pilot.pause()
            arguments = _creation_arguments(screen.values(), "generate", "session")
            assert arguments["operation"] == "edit"
            assert arguments["inputs"] == ["artifact:art_test"]
            assert arguments["reference_options"] == [{"kind": "vibe", "settings": {}}]
            assert arguments["provider_params"] == {
                "scale": 5.5,
                "strength": 0.5,
                "noise": 0.1,
                "sm": False,
                "negative_prompt": "blur",
            }
            assert calls == []
            import json

            ids = ["art_" + "1" * 32, "art_" + "2" * 32]
            opts = [
                {"kind": "vibe", "settings": {"strength": 0.4}},
                {"kind": "vibe", "settings": {"strength": 0.8}},
            ]
            screen.query_one("#nai_references", Input).value = json.dumps(ids)
            screen.query_one("#nai_reference_options", Input).value = json.dumps(opts)
            preview = _creation_arguments(screen.values(), "generate", "session")
            assert preview["references"] == ["artifact:" + item for item in ids]
            assert preview["reference_options"] == opts
            assert calls == []
            screen.query_one("#profile", Select).value = "old"
            await pilot.pause()
            assert not screen.query_one("#nai_scale", Input).display
            assert not any(key.startswith("nai_") for key in screen.values())
            screen.query_one("#profile", Select).value = "nai"
            await pilot.pause()
            assert calls == []
            screen.query_one("#nai-vibe-status").press()
            await pilot.pause()
            assert calls == ["status"]

    asyncio.run(run())


def test_advanced_json_cannot_supply_image_paths():
    import pytest

    from imagen_cli.cli.tui.novelai_forms import parameters

    with pytest.raises(ValueError):
        parameters(
            {"nai_v4_prompt": '{"image":"/tmp/private.png"}'}, operation="generate", model="manual"
        )


def test_ordered_references_refuse_paths_and_misalignment():
    import pytest

    from imagen_cli.cli.tui.novelai_forms import reference

    for refs, options in [
        ('["/tmp/private.png"]', '[{"kind":"vibe"}]'),
        ('["art_' + "1" * 32 + '"]', "[]"),
    ]:
        with pytest.raises(ValueError):
            reference({"nai_references": refs, "nai_reference_options": options})


def test_workbench_preparation_forms_plan_execute_and_reuse(tmp_path, png_bytes):
    from imagen_cli.application.container import Container
    from imagen_cli.cli.commands.vibe import execute
    from imagen_cli.cli.tui.app import Deps, ResultScreen, WorkbenchApp
    from tests.unit.test_novelai_vibe_cache import setup_vibe

    with Container(workspace=str(tmp_path)) as container:
        service, profile, identity, encoder = setup_vibe(container, png_bytes)
        calls = []

        def run_command(command, arguments):
            calls.append((command, arguments))
            from imagen_cli.application.novelai_vibe_service import VibePreparationService

            with Container(workspace=str(tmp_path)) as worker:
                local = VibePreparationService(
                    worker.repositories, worker.artifact_store, worker.private_state_store,
                    adapter_factory=lambda _: encoder,
                )
                return execute(local, command, arguments)

        async def run():
            app = WorkbenchApp(Deps(container.repositories, container.artifact_store,
                                    execute=run_command))
            async with app.run_test(size=(120, 50)) as pilot:
                await pilot.pause()
                async def open_preparation(action):
                    app.push_screen(app._creation_form(
                        "Generate", [("prompt", "", "kite"),
                                     ("profile", "", profile.id),
                                     ("model", "", profile.model_default),
                                     ("count", "", "1")], optional=set(),
                    ))
                    await pilot.pause()
                    app.screen.query_one("#nai-vibe-" + action).press()
                    await pilot.pause()
                    assert app.screen.heading.endswith("vibe " + action), type(app.screen)

                await open_preparation("plan")
                for key, value in {"artifact_id": identity, "profile": profile.id,
                                   "model": profile.model_default,
                                   "information_extracted": "0.6"}.items():
                    app.screen.query_one("#" + key, Input).value = value
                app.screen.submit()
                await pilot.pause()
                await app.workers.wait_for_complete()
                await pilot.pause()
                assert isinstance(app.screen, ResultScreen)
                assert "preparation" in app.screen.data, app.screen.data
                row = app.screen.data["preparation"]
                assert row["digest"] and row["charge_disclosure"]
                assert calls[0][1]["information_extracted"] == 0.6
                assert not encoder.calls
                for _ in range(2):
                    app.screen.action_close()
                    await pilot.pause()
                    await open_preparation("execute")
                    app.screen.query_one("#preparation_id", Input).value = row["id"]
                    app.screen.query_one("#digest", Input).value = row["digest"]
                    app.screen.submit()
                    await pilot.pause()
                    await app.workers.wait_for_complete()
                    await pilot.pause()
                    assert isinstance(app.screen, ResultScreen)
                    assert app.screen.data["preparation"]["state"] == "succeeded"
                assert len(encoder.calls) == 1

        asyncio.run(run())


def test_parameter_error_names_field_without_echoing_input():
    import pytest

    from imagen_cli.cli.tui.novelai_forms import parameters

    with pytest.raises(ValueError, match="v4_prompt") as error:
        parameters({"nai_v4_prompt": '{"private-token":'}, operation="generate", model="manual")
    assert "private-token" not in str(error.value)


def test_existing_edit_form_validates_as_edit_without_new_primary():
    from imagen_cli.cli.tui.forms import CreationForm

    results = []

    class Harness(App):
        def on_mount(self):
            self.push_screen(CreationForm(
                "Edit existing artifact",
                [("prompt", "", "draw"), ("profile", "", "nai"),
                 ("model", "", "manual"), ("count", "", "1")],
                profiles=[{"id": "nai", "provider": "novelai", "family": "generate_image"}],
                models={}, operation="edit",
            ), results.append)

    async def run():
        async with Harness().run_test(size=(100, 40)) as pilot:
            screen = pilot.app.screen
            screen.submit()
            await pilot.pause()
            assert not results
            screen.query_one("#nai_strength", Input).value = "0.5"
            screen.query_one("#nai_noise", Input).value = "0.1"
            screen.submit()
            await pilot.pause()
            assert len(results) == 1
            assert not results[0].get("nai_primary")

    asyncio.run(run())
