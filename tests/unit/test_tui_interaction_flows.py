"""Mouse and keyboard paths operate on the same selected items and confirmations."""

import asyncio
import io
from pathlib import Path

import pytest
from PIL import Image
from textual.widgets import Button, Input

from imagen_cli.application.container import Container
from imagen_cli.application.session_service import SessionService
from imagen_cli.cli.tui.app import (
    CacheScreen,
    ConfirmScreen,
    Deps,
    FormScreen,
    ParamEditScreen,
    WorkbenchApp,
)
from imagen_cli.domain.primitive import new_id


def artifact(container, color="red"):
    data = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(data, format="PNG")
    identity = new_id("art")
    container.artifact_store.stage(identity, data.getvalue())
    container.artifact_store.publish(identity)
    return identity, container.artifact_store.get(identity)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with Container(workspace=str(tmp_path / "workspace")) as container:
        service = SessionService(container.repositories, container.artifact_store)
        session = service.create_session("a")
        yield container, session


@pytest.mark.parametrize("size", [(80, 24), (140, 40)])
def test_import_and_save_have_reachable_mouse_buttons(workspace, size):
    container, session = workspace
    identity, path = artifact(container)
    container.repositories.insert("artifact_links", {
        "session_id": session.id, "artifact_id": identity, "role": "output",
        "created_at": "2026-10-30T00:00:00Z",
    })

    async def exercise():
        app = WorkbenchApp(Deps(container.repositories, container.artifact_store))
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            assert await pilot.click("#action-import")
            await pilot.pause()
            assert app.screen.query_one("#picker-path", Input).value
            await pilot.click("#picker-cancel")
            await pilot.pause()
            assert app.focused.id == "action-import"
            assert await pilot.click("#action-save")
            await pilot.pause()
            assert identity in app.screen.query_one("#picker-name", Input).value
            await pilot.click("#picker-cancel")
            await pilot.pause()
            assert app.focused.id == "action-save"
            assert path.exists()

    asyncio.run(exercise())


def test_more_menu_provides_mouse_cache_and_returns_to_trigger(workspace):
    container, _ = workspace

    async def exercise():
        app = WorkbenchApp(Deps(container.repositories, container.artifact_store))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.click("#action-more")
            await pilot.pause()
            cache = app.screen.query_one("#more-cache", Button)
            cache.scroll_visible(animate=False)
            await pilot.pause()
            assert await pilot.click("#more-cache")
            await pilot.pause()
            assert isinstance(app.screen, CacheScreen)
            await pilot.click("#cache-close")
            await pilot.pause()
            assert app.focused.id == "action-more"

    asyncio.run(exercise())


def test_more_menu_keyboard_skips_disabled_image_actions(workspace):
    container, _ = workspace

    async def exercise():
        app = WorkbenchApp(Deps(container.repositories, container.artifact_store))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("f10")
            await pilot.pause()
            assert app.focused.id == "more-export"
            await pilot.press("down")
            await pilot.pause()
            assert app.focused.id == "more-cache"
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, CacheScreen)
            assert app.screen.query_one("#cache-purge", Button).disabled
            await pilot.press("escape")
            await pilot.pause()
            assert app.focused is app.sessions

    asyncio.run(exercise())


def test_cache_yes_no_deletes_only_the_displayed_orphans(workspace):
    container, session = workspace
    orphan, orphan_path = artifact(container)
    owned, owned_path = artifact(container, "blue")
    container.repositories.insert("artifact_links", {
        "session_id": session.id, "artifact_id": owned, "role": "output",
        "created_at": "2026-10-30T00:00:00Z",
    })

    async def exercise():
        app = WorkbenchApp(Deps(container.repositories, container.artifact_store))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("c")
            await pilot.pause()
            cache = app.screen
            assert [entry.identity for entry in cache.entries] == [orphan]
            await pilot.click("#cache-purge")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)
            assert orphan_path.exists() and owned_path.exists()
            await pilot.click("#confirm-no")
            await pilot.pause()
            assert app.screen is cache and orphan_path.exists()
            assert app.focused.id == "cache-purge"
            await pilot.click("#cache-purge")
            await pilot.pause()
            await pilot.click("#confirm-yes")
            await pilot.pause()
            assert app.screen is cache
            assert not orphan_path.exists() and owned_path.exists()
            assert container.repositories.get("artifacts", orphan) is None
            assert cache.query_one("#cache-purge", Button).disabled
            assert cache.table.row_count == 0

    # Every delete is confined to the temporary workspace supplied by pytest.
    asyncio.run(exercise())


def test_cache_changed_plan_is_not_silently_confirmed(workspace):
    container, _ = workspace
    _, original = artifact(container)

    async def exercise():
        app = WorkbenchApp(Deps(container.repositories, container.artifact_store))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("c")
            await pilot.pause()
            cache = app.screen
            await pilot.press("p")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)
            _, newly_added = artifact(container, "green")
            await pilot.press("y")
            await pilot.pause()
            assert app.screen is cache
            assert original.exists() and newly_added.exists()
            assert len(cache.entries) == 2
            assert not cache.query_one("#cache-purge", Button).disabled
            assert "digest mismatch" not in str(cache.status.render())

    asyncio.run(exercise())


def test_cache_reports_partial_failure_without_closing(workspace, monkeypatch):
    container, _ = workspace
    _, blocked = artifact(container)
    _, removable = artifact(container, "green")
    unlink = Path.unlink

    def fail_one(path, *args, **kwargs):
        if path == blocked:
            raise PermissionError("test file is locked")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_one)

    async def exercise():
        app = WorkbenchApp(Deps(container.repositories, container.artifact_store))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("c")
            await pilot.pause()
            cache = app.screen
            await pilot.press("p")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.press("y")
            await pilot.pause()
            assert app.screen is cache
            assert blocked.exists() and not removable.exists()
            assert len(cache.entries) == 1
            assert not cache.query_one("#cache-purge", Button).disabled
            assert "1" in str(cache.status.render())

    asyncio.run(exercise())


@pytest.mark.parametrize("keys, result", [
    (["enter"], False), (["tab", "enter"], True),
    (["right", "enter"], True), (["right", "left", "enter"], False),
    (["escape"], False),
])
def test_confirmation_uses_the_focused_button(workspace, keys, result):
    container, _ = workspace
    answers = []

    async def exercise():
        app = WorkbenchApp(Deps(container.repositories, container.artifact_store))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await app.push_screen(ConfirmScreen("Only a probe"), answers.append)
            await pilot.pause()
            assert app.focused.id == "confirm-no"
            await pilot.press(*keys)
            await pilot.pause()
            assert answers == [result]

    asyncio.run(exercise())


def test_delete_confirmation_keeps_its_original_session(workspace):
    container, session = workspace
    other = SessionService(container.repositories, container.artifact_store).create_session("b")

    async def exercise():
        app = WorkbenchApp(Deps(container.repositories, container.artifact_store))
        async with app.run_test(size=(80, 24)) as pilot:
            app.current = session.id
            await pilot.press("x")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)
            app.current = other.id
            await pilot.click("#confirm-yes")
            await pilot.pause()
            assert container.repositories.get("sessions", session.id)["deleted_at"] is not None
            assert container.repositories.get("sessions", other.id).get("deleted_at") is None

    asyncio.run(exercise())


def test_save_dialog_keeps_its_original_image(workspace, tmp_path):
    container, session = workspace
    first, first_path = artifact(container)
    second, _ = artifact(container, "blue")
    for identity in (first, second):
        container.repositories.insert("artifact_links", {
            "session_id": session.id, "artifact_id": identity, "role": "output",
            "created_at": "2026-10-30T00:00:00Z",
        })
    destination = tmp_path / "chosen.png"

    async def exercise():
        app = WorkbenchApp(Deps(container.repositories, container.artifact_store))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            app._select_artifact(0)
            await pilot.click("#action-save")
            await pilot.pause()
            app._select_artifact(1)
            app.screen.query_one("#picker-name", Input).value = str(destination)
            await pilot.click("#picker-confirm")
            await pilot.pause()
            assert destination.read_bytes() == first_path.read_bytes()

    asyncio.run(exercise())


@pytest.mark.parametrize("operation", ["import", "export"])
def test_file_dialog_keeps_its_original_session(workspace, tmp_path, operation):
    import json
    from zipfile import ZipFile

    container, session = workspace
    other = SessionService(container.repositories, container.artifact_store).create_session("b")
    source = tmp_path / "source.png"
    Image.new("RGB", (8, 8), "red").save(source)
    destination = tmp_path / "session.zip"

    async def exercise():
        app = WorkbenchApp(Deps(container.repositories, container.artifact_store))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            app.current = session.id
            if operation == "import":
                app.action_import_image()
            else:
                app.action_export()
            await pilot.pause()
            app.current = other.id
            if operation == "import":
                await app.screen.dismiss(source)
            else:
                app.screen.query_one("#destination", Input).value = str(destination)
                await pilot.click("#form-submit")
            await pilot.pause()
            if operation == "import":
                links = container.repositories.list("artifact_links", session_id=session.id)
                assert len(links) == 1
                assert container.repositories.list("artifact_links", session_id=other.id) == []
            else:
                with ZipFile(destination) as archive:
                    assert json.loads(archive.read("manifest.json"))["session"]["id"] == session.id

    asyncio.run(exercise())


def test_model_parameter_enter_moves_focus_without_running(workspace):
    from imagen_cli.cli.tui.model_process import ModelProcessScreen

    container, session = workspace
    identity, path = artifact(container)

    async def exercise():
        app = WorkbenchApp(Deps(container.repositories, container.artifact_store))
        async with app.run_test(size=(100, 35)) as pilot:
            screen = ModelProcessScreen(app.deps, identity, session.id, path)
            await app.push_screen(screen)
            index = next(i for i, row in enumerate(screen.rows)
                         if row["model"] == "realesr-animevideov3")
            screen.table.move_cursor(row=index)
            await pilot.pause()
            screen.tile.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert app.focused is screen.overlap
            assert not screen.running
            assert container.repositories.list("jobs") == []

    asyncio.run(exercise())


def test_parameter_enter_does_not_apply_the_pipeline(workspace):
    container, _ = workspace
    answers = []

    async def exercise():
        app = WorkbenchApp(Deps(container.repositories, container.artifact_store))
        async with app.run_test(size=(80, 24)) as pilot:
            screen = ParamEditScreen("Contrast", [{"op": "contrast", "params": {"factor": 1.1}}])
            await app.push_screen(screen, answers.append)
            await pilot.pause()
            assert isinstance(app.focused, Input)
            await pilot.press("enter")
            await pilot.pause()
            assert app.screen is screen and not answers
            await pilot.press("ctrl+enter")
            await pilot.pause()
            assert len(answers) == 1
            assert answers[0][0]["params"]["factor"] == 1.1

    asyncio.run(exercise())


def test_plain_form_enter_moves_focus_without_submitting(workspace):
    container, _ = workspace
    answers = []

    async def exercise():
        app = WorkbenchApp(Deps(container.repositories, container.artifact_store))
        async with app.run_test(size=(80, 24)) as pilot:
            form = FormScreen("Probe", [("first", "", "a"), ("second", "", "b")])
            await app.push_screen(form, answers.append)
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert app.screen is form and not answers
            assert app.focused.id == "second"
            await pilot.press("enter")
            await pilot.pause()
            assert app.screen is form and app.focused.id == "form-submit"
            await pilot.press("enter")
            await pilot.pause()
            assert answers == [{"first": "a", "second": "b"}]

    asyncio.run(exercise())
