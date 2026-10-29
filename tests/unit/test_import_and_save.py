import io
from pathlib import Path, PureWindowsPath

import pytest
from PIL import Image
from textual.app import App

from imagen_cli.application import transfer_service
from imagen_cli.application.container import Container
from imagen_cli.application.session_service import SessionService
from imagen_cli.application.transfer_service import import_image, save_artifact_copy
from imagen_cli.cli.commands import asset, history
from imagen_cli.cli.tui.app import Deps, FilePickerScreen, WorkbenchApp
from imagen_cli.storage import queries


def _png(color="red", size=(8, 8)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def workspace(tmp_path):
    with Container(workspace=str(tmp_path / "workspace")) as container:
        service = SessionService(container.repositories, container.artifact_store)
        session = service.create_session("imports")
        yield container, service, session


def test_import_image_links_a_published_artifact(workspace, tmp_path):
    container, _, session = workspace
    source = tmp_path / "outside.png"
    original = _png("blue")
    source.write_bytes(original)

    data = import_image(
        container.repositories, container.artifact_store, session.id, source,
    )

    identity = data["artifact"]["id"]
    assert data["session"] == session.id
    assert source.read_bytes() == original
    assert container.artifact_store.get(identity).read_bytes() == original
    outputs = queries.session_outputs(container.repositories, session.id)
    assert [row["id"] for row in outputs] == [identity]
    assert outputs[0]["origin_manifest"]["kind"] == "imported"


def test_import_image_rejects_missing_unknown_and_non_image(workspace, tmp_path):
    container, _, session = workspace
    missing = tmp_path / "missing.png"
    with pytest.raises(FileNotFoundError):
        import_image(container.repositories, container.artifact_store, session.id, missing)
    with pytest.raises(KeyError):
        import_image(
            container.repositories, container.artifact_store, "ses_unknown", _write(tmp_path),
        )
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not an image")
    with pytest.raises(ValueError):
        import_image(container.repositories, container.artifact_store, session.id, broken)


def test_import_image_rejects_oversized_files(workspace, tmp_path, monkeypatch):
    container, _, session = workspace
    monkeypatch.setattr(transfer_service, "MAX_BYTES", 10)
    source = _write(tmp_path)
    with pytest.raises(ValueError):
        import_image(container.repositories, container.artifact_store, session.id, source)


def test_save_artifact_copies_verified_bytes(workspace, tmp_path):
    container, _, session = workspace
    data = import_image(
        container.repositories, container.artifact_store, session.id, _write(tmp_path),
    )
    destination = tmp_path / "saved.png"

    saved = save_artifact_copy(
        container.repositories, container.artifact_store, data["artifact"]["id"], destination,
    )

    assert saved["path"] == str(destination.absolute())
    assert destination.read_bytes() == container.artifact_store.get(
        data["artifact"]["id"]
    ).read_bytes()
    assert container.artifact_store.get(data["artifact"]["id"]).exists()


def test_save_artifact_never_overwrites(workspace, tmp_path):
    container, _, session = workspace
    data = import_image(
        container.repositories, container.artifact_store, session.id, _write(tmp_path),
    )
    destination = tmp_path / "saved.png"
    destination.write_bytes(b"keep me")
    with pytest.raises(FileExistsError):
        save_artifact_copy(
            container.repositories, container.artifact_store,
            data["artifact"]["id"], destination,
        )
    assert destination.read_bytes() == b"keep me"
    with pytest.raises(ValueError):
        save_artifact_copy(
            container.repositories, container.artifact_store,
            data["artifact"]["id"], tmp_path / "absent" / "saved.png",
        )
    with pytest.raises(ValueError):
        save_artifact_copy(
            container.repositories, container.artifact_store, data["artifact"]["id"], tmp_path,
        )


def test_cli_handlers_report_statuses(workspace, tmp_path):
    container, _, session = workspace
    source = _write(tmp_path)
    status, data = history.import_image_(
        container.repositories, container.artifact_store, session.id, str(source),
    )
    assert status == "succeeded"
    identity = data["artifact"]["id"]
    assert history.import_image_(
        container.repositories, container.artifact_store, "ses_missing", str(source),
    )[0] == "not_found"
    assert history.import_image_(
        container.repositories, container.artifact_store, session.id, str(tmp_path / "no.png"),
    )[0] == "not_found"
    destination = tmp_path / "copy.png"
    assert asset.save_(
        container.repositories, container.artifact_store, identity, str(destination),
    )[0] == "succeeded"
    assert asset.save_(
        container.repositories, container.artifact_store, identity, str(destination),
    )[0] == "conflict"


def test_cli_parser_accepts_both_commands():
    from imagen_cli.cli.entrypoint import _parse

    command, args, _ = _parse(["session", "import-image", "ses_a", "photo.png"])
    assert command == "session import-image"
    assert args == {"session": "ses_a", "path": "photo.png"}
    command, args, _ = _parse(["asset", "save", "art_" + "a" * 32, "out.png"])
    assert command == "asset save"
    assert args == {"artifact": "art_" + "a" * 32, "path": "out.png"}


def test_file_picker_lists_folders_then_images(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.png").write_bytes(_png())
    (tmp_path / "notes.txt").write_text("x")

    async def scenario():
        app = App()
        async with app.run_test(size=(80, 24)) as pilot:
            picker = FilePickerScreen(start=tmp_path, mode="open")
            app.push_screen(picker)
            await pilot.pause()
            assert [path.name for path in picker.entries()] == ["sub", "a.png"]
            picker.action_cancel()

    import asyncio

    asyncio.run(scenario())


def test_file_picker_save_mode_builds_a_new_name(tmp_path):
    import asyncio

    async def scenario():
        app = App()
        chosen: list[Path | None] = []
        async with app.run_test(size=(80, 24)) as pilot:
            picker = FilePickerScreen(start=tmp_path, mode="save", filename="copy.png")
            app.push_screen(picker, chosen.append)
            await pilot.pause()
            picker.action_confirm()
            await pilot.pause()
        return chosen

    assert asyncio.run(scenario()) == [tmp_path.resolve() / "copy.png"]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (PureWindowsPath("C:/Users/me"), PureWindowsPath("C:/Users")),
        (PureWindowsPath("C:/"), None),
        (Path("/tmp/x"), Path("/tmp")),
        (Path("/"), None),
    ],
)
def test_parent_stops_at_the_root(path, expected):
    assert transfer_service.parent_of(path) == expected


def test_tui_import_and_save_actions(workspace, tmp_path):
    import asyncio

    container, _, session = workspace
    source = _write(tmp_path)
    destination = tmp_path / "saved.png"

    async def scenario():
        app = WorkbenchApp(Deps(container.repositories, container.artifact_store))
        async with app.run_test(size=(100, 30)) as pilot:
            app.current = session.id
            await pilot.pause()
            app._import_chosen(source)
            await pilot.pause()
            items = app._load_artifacts(session.id)
            assert len(items) == 1
            app.film_items = items
            app.film_index = 0
            app._save_chosen(destination)
            await pilot.pause()

    asyncio.run(scenario())
    assert destination.read_bytes() == source.read_bytes()


def _write(directory: Path) -> Path:
    path = directory / f"image-{len(list(directory.glob('image-*')))}.png"
    path.write_bytes(_png())
    return path
