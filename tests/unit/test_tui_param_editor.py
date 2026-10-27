"""Typed controls and validation for the transform parameter editor."""

import asyncio
import copy

import pytest
from textual.app import App
from textual.widgets import Input, Select, Static

from imagen_cli.application.operator_catalog import get_operator_item
from imagen_cli.cli.tui.app import ParamEditScreen


def test_mouse_can_save_parameters():
    async def exercise():
        app = App()
        saved = []
        async with app.run_test(size=(120, 40)) as pilot:
            screen = ParamEditScreen("test", [{"op": "film_grain", "params": {}}])
            await app.push_screen(screen, saved.append)
            await pilot.click("#param-save")
            await pilot.pause()
            assert saved == [[{"op": "film_grain", "params": {}}]]

    asyncio.run(exercise())


def test_tile_catalog_has_integer_choices():
    item = get_operator_item("superres")
    tile = next(p for p in item.parameters if p.name == "tile")
    assert type(tile.default) is int
    assert tile.choices == [128, 256, 512]
    assert all(type(value) is int for value in tile.choices)


def test_choice_and_boolean_controls_preserve_typed_values():
    async def exercise():
        app = App()
        async with app.run_test():
            screen = ParamEditScreen("test", [
                {"op": "superres", "params": {"tile": 256}},
                {"op": "film_grain", "params": {"monochrome": True}},
                {"op": "kuwahara_anisotropic", "params": {"sectors": 8}},
            ])
            await app.push_screen(screen)
            tile = screen.query_one("#f_0_tile", Select)
            monochrome = screen.query_one("#f_1_monochrome", Select)
            sectors = screen.query_one("#f_2_sectors", Select)
            tile.value = 512
            monochrome.value = False
            sectors.value = 12
            result = screen._save_inputs_to_pipeline()
            assert result[0]["params"]["tile"] == 512
            assert type(result[0]["params"]["tile"]) is int
            assert result[1]["params"]["monochrome"] is False
            assert type(result[2]["params"]["sectors"]) is int

    asyncio.run(exercise())


@pytest.mark.parametrize("pipeline", [
    [{"op": "superres", "params": {"tile": 256}}],
    [{"op": "film_grain", "params": {}}],
    [{"op": "superres"}],
    [{"op": "unknown", "params": {"text": "  keep spaces  ", "enabled": False}}],
])
def test_unchanged_editor_roundtrips_without_adding_defaults(pipeline):
    original = copy.deepcopy(pipeline)

    async def exercise():
        app = App()
        async with app.run_test():
            screen = ParamEditScreen("test", pipeline)
            await app.push_screen(screen)
            assert screen._save_inputs_to_pipeline() == original
            assert screen.pipeline == original
            assert pipeline == original

    asyncio.run(exercise())


@pytest.mark.parametrize(("op", "key", "value"), [
    ("film_grain", "amount", "not-a-number"),
    ("film_grain", "amount", "nan"),
    ("film_grain", "amount", "inf"),
    ("film_grain", "amount", "99"),
    ("film_grain", "amount", ""),
    ("superres", "overlap", "1.5"),
    ("film_grain", "monochrome", "not-a-bool"),
    ("superres", "tile", "256"),
])
def test_invalid_parameter_shows_error_without_dismissing(op, key, value):
    async def exercise():
        app = App()
        async with app.run_test() as pilot:
            params = {key: value} if key in ("monochrome", "tile") else {}
            screen = ParamEditScreen("test", [{"op": op, "params": params}])
            await app.push_screen(screen)
            if key not in ("monochrome", "tile"):
                screen.query_one(f"#f_0_{key}", Input).value = value
            await pilot.press("ctrl+enter")
            assert app.screen is screen
            error = screen.query_one("#param-error", Static)
            assert key in str(error.render())
            assert error.visible

    asyncio.run(exercise())


def test_keyboard_can_reach_select_choose_and_confirm():
    async def exercise():
        app = App()
        saved = []
        async with app.run_test(size=(120, 40)) as pilot:
            screen = ParamEditScreen("test", [{
                "op": "superres",
                "params": {"model": "realesrgan-x4plus-anime", "tile": 256},
            }])
            await app.push_screen(screen, saved.append)
            await pilot.press("tab")
            tile = screen.query_one("#f_0_tile", Select)
            assert screen.focused is tile
            await pilot.press("enter", "down", "enter")
            assert tile.value == 512
            assert app.screen is screen
            await pilot.press("ctrl+enter")
            assert app.screen is not screen
            assert saved[0][0]["params"]["tile"] == 512

    asyncio.run(exercise())
