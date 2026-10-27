"""The transform panel and CLI must expose the same recipes."""

from imagen_cli.application.operator_catalog import PRESET_PIPELINES
from imagen_cli.cli.tui.app import PRESETS


def test_transform_mouse_selects_category_and_preset(tmp_path):
    import asyncio

    from textual.app import App
    from textual.widgets import Button

    from imagen_cli.cli.tui.app import TransformScreen, WorkbenchApp

    class Harness(App):
        CSS = WorkbenchApp.CSS

    async def exercise():
        app = Harness()
        async with app.run_test(size=(120, 40)) as pilot:
            screen = TransformScreen(None, "art_test", "ses_test", tmp_path / "missing.png")
            await app.push_screen(screen)
            await pilot.pause()
            await pilot.click(screen.category_table, offset=(2, 2))
            await pilot.pause()
            assert screen.current_category_idx == 1
            assert screen.filtered_names != screen.all_names
            assert screen.query_one("#transform-apply", Button)
            await pilot.click("#transform-cancel")
            await pilot.pause()
            assert app.screen is not screen

    asyncio.run(exercise())


def test_tui_uses_shared_recipe_catalog():
    assert PRESETS is PRESET_PIPELINES


def test_transform_screen_has_inflight_cancel_state(tmp_path):
    from imagen_cli.cli.tui.app import TransformScreen

    screen = TransformScreen(None, "art_test", "ses_test", tmp_path / "source.png")
    screen.running = True
    screen.action_cancel()
    assert screen.cancelled.is_set()


def test_transform_unmount_cancels_inflight_work(tmp_path):
    import asyncio

    from textual.app import App

    from imagen_cli.cli.tui.app import TransformScreen

    async def exercise():
        app = App()
        async with app.run_test():
            screen = TransformScreen(None, "art_test", "ses_test", tmp_path / "missing.png")
            await app.push_screen(screen)
            screen.running = True
            await app.pop_screen()
            assert screen.cancelled.is_set()

    asyncio.run(exercise())


def test_integer_choice_survives_parameter_editor():
    import asyncio

    from textual.app import App

    from imagen_cli.cli.tui.app import ParamEditScreen

    async def exercise():
        app = App()
        async with app.run_test():
            screen = ParamEditScreen("test", [{"op": "kuwahara_anisotropic", "params": {}}])
            for field, widget in zip(screen.fields_info, screen.inputs, strict=True):
                if field[5] == "sectors":
                    widget.value = 12
            assert screen._save_inputs_to_pipeline()[0]["params"]["sectors"] == 12

    asyncio.run(exercise())
