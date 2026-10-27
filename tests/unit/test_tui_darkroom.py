"""Darkroom controls remain readable and mouse-accessible in small terminals."""

import asyncio

from textual.app import App
from textual.widgets import Button

from imagen_cli.cli.tui.app import WorkbenchApp
from imagen_cli.cli.tui.forms import ChoiceForm


def test_action_buttons_have_room_for_text_and_borders():
    class Harness(App):
        CSS = WorkbenchApp.CSS

        def compose(self):
            from textual.containers import Horizontal

            yield Horizontal(Button("Edit", id="action-edit"), id="action-bar")

    async def exercise():
        app = Harness()
        async with app.run_test(size=(80, 24)):
            button = app.query_one(Button)
            assert button.content_size.height >= 1

    asyncio.run(exercise())


def test_compact_button_labels_fit_on_one_line():
    from textual.containers import Horizontal, Vertical

    class Harness(App):
        CSS = WorkbenchApp.CSS

        def compose(self):
            with Horizontal(id="body", classes="compact"):
                with Vertical(id="sidebar"):
                    yield Horizontal(Button("New"), Button("Providers"), classes="sidebar-actions")
                with Vertical(id="detail-pane"):
                    yield Horizontal(
                        *(Button(label, id=f"action-{identity}") for identity, label in [
                            ("generate", "Generate"), ("edit", "Edit"),
                            ("transform", "Transform"), ("open", "Open"),
                            ("prev", "‹"), ("next", "›"),
                        ]), id="action-bar",
                    )

    async def exercise():
        app = Harness()
        async with app.run_test(size=(80, 24)):
            for button in app.query(Button):
                assert button.content_size.width >= len(str(button.label))
                assert button.region.right <= 80

    asyncio.run(exercise())


def test_choice_form_mouse_cancel_does_not_submit():
    async def exercise():
        app = App()
        results = []
        async with app.run_test(size=(80, 24)) as pilot:
            screen = ChoiceForm("Test", [("name", "Name", "draft")])
            await app.push_screen(screen, results.append)
            await pilot.click("#cancel")
            await pilot.pause()
            assert results == [None]

    asyncio.run(exercise())


def test_cache_mouse_purge_requires_digest_and_typed_confirmation():
    from imagen_cli.cli.tui.app import CacheScreen, Deps

    calls = []
    deps = Deps(None, None, audit=lambda *args, **kwargs: [],
                purge=lambda *args, **kwargs: calls.append(args) or {"deleted_bytes": 0})

    class Harness(App):
        CSS = WorkbenchApp.CSS

    async def exercise():
        app = Harness()
        async with app.run_test(size=(80, 24)) as pilot:
            screen = CacheScreen(deps)
            await app.push_screen(screen)
            await pilot.click("#cache-purge")
            assert calls == []
            await pilot.click("#cache-plan")
            await pilot.pause(0.4)
            await pilot.click("#cache-purge")
            assert calls == []
            screen.field.value = "purge"
            await pilot.pause(0.4)
            await pilot.click("#cache-purge")
            await pilot.pause()
            assert len(calls) == 1

    asyncio.run(exercise())


def test_transform_compact_preview_and_details_stay_above_actions(tmp_path):
    from PIL import Image

    from imagen_cli.cli.tui.app import Deps, TransformScreen

    path = tmp_path / "source.png"
    Image.new("RGBA", (120, 80), (255, 0, 0, 0)).save(path)

    class Harness(App):
        CSS = WorkbenchApp.CSS

    async def exercise():
        app = Harness()
        async with app.run_test(size=(80, 24)) as pilot:
            screen = TransformScreen(Deps(None, None), "art", "session", path)
            screen.add_class("compact")
            await app.push_screen(screen)
            await pilot.pause()
            actions = screen.query_one(".dialog-actions")
            assert screen.view.content_size.height >= 2
            assert screen.status.region.bottom <= actions.region.y
            assert screen.params_view.region.bottom <= actions.region.y

    asyncio.run(exercise())


def test_transform_preview_preserves_alpha_and_available_size(tmp_path, monkeypatch):
    from PIL import Image

    from imagen_cli.cli.tui import preview
    from imagen_cli.cli.tui.app import Deps, TransformScreen

    path = tmp_path / "transparent.png"
    Image.new("RGBA", (120, 80), (255, 0, 0, 0)).save(path)
    calls = []
    render = preview.ansi_image

    def capture(image, *, cols, rows):
        calls.append((image.mode, cols, rows))
        return render(image, cols=cols, rows=rows)

    monkeypatch.setattr(preview, "ansi_image", capture)

    class Harness(App):
        CSS = WorkbenchApp.CSS

    async def exercise():
        app = Harness()
        async with app.run_test(size=(80, 24)) as pilot:
            screen = TransformScreen(Deps(None, None), "art", "session", path)
            screen.add_class("compact")
            await app.push_screen(screen)
            await pilot.pause()
            screen.pipelines[screen.names[screen.table.cursor_row]] = []
            screen.render_preview()
            mode, cols, rows = calls[-1]
            assert mode == "RGBA"
            assert cols <= screen.view.content_size.width
            assert rows <= screen.view.content_size.height
            await pilot.resize_terminal(120, 40)
            await pilot.pause()
            assert calls[-1][2] == min(28, screen.view.content_size.height)
            screen.running = True
            original = screen.table.cursor_row
            screen.action_cursor_down()
            screen.action_edit_params()
            assert screen.table.cursor_row == original
            assert app.screen is screen

    asyncio.run(exercise())


def test_providers_manual_model_button_uses_existing_form(tmp_path):
    from imagen_cli.cli.tui.app import Deps, FormScreen, ProvidersScreen
    from imagen_cli.storage.database import connect
    from imagen_cli.storage.repositories import Repositories

    repos = Repositories(connect(tmp_path / "state.sqlite3"))
    repos.insert("profiles", {"id": "local", "provider": "openai", "family": "images",
                             "base_url": "http://localhost", "model_default": "test",
                             "credential_ref": "env:TEST", "revision": 1})

    class Harness(App):
        CSS = WorkbenchApp.CSS

    async def exercise():
        app = Harness()
        async with app.run_test(size=(80, 24)) as pilot:
            await app.push_screen(ProvidersScreen(Deps(repos, None, models_add=lambda *args: None)))
            await pilot.click("#providers-manual")
            await pilot.pause()
            assert isinstance(app.screen, FormScreen)

    try:
        asyncio.run(exercise())
    finally:
        repos.connection.close()


def test_information_dialogs_can_close_with_mouse():
    from imagen_cli.cli.tui.app import HelpScreen, ResultScreen

    async def exercise():
        app = App()
        async with app.run_test(size=(80, 24)) as pilot:
            for screen in [HelpScreen([("q", "Quit")]), ResultScreen("test", "succeeded", {})]:
                results = []
                await app.push_screen(screen, results.append)
                await pilot.click(".dialog-close")
                await pilot.pause()
                assert results == [None]

    asyncio.run(exercise())

