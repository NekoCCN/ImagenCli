"""TUI pilot: read-only browsing, typed confirmation, no writes before it."""

import asyncio
from pathlib import Path

import pytest
from PIL import Image

from imagen_cli.application import cache_service
from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.database import connect
from imagen_cli.storage.repositories import Repositories

pytest.importorskip("textual")

from imagen_cli.cli.tui.app import Deps, WorkbenchApp  # noqa: E402


class Recorder:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.purges: list[str] = []

    def audit(self, repositories, store):
        return [
            cache_service.Entry("unreferenced_artifact", "art_" + "a" * 32, 10, {}),
        ]

    def purge(self, repositories, store, digest):
        self.purges.append(digest)
        return {"deleted_bytes": 10, "results": []}

    def delete(self, repositories, identity):
        self.deleted.append(identity)
        return {}


@pytest.fixture
def deps(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    repositories = Repositories(connect(root / "state.sqlite3"))
    store = ArtifactStore(root / "artifacts", repositories)
    for index in range(100):
        repositories.insert("sessions", {
            "id": f"ses_{index:032x}", "name": f"session-{index}",
            "version": index, "created_at": "2026-10-24T00:00:00Z",
        })
    recorder = Recorder()
    yield Deps(repositories, store, recorder.audit, recorder.purge, recorder.delete), recorder
    repositories.connection.close()


def test_lists_one_hundred_sessions_at_80x24(deps):
    deps_value, _ = deps

    app = WorkbenchApp(deps_value)

    async def run():
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert app.sessions.row_count == 100
            await pilot.press("q")

    asyncio.run(run())


def test_cache_panel_writes_nothing_before_confirmation(deps):
    deps_value, recorder = deps

    async def run():
        async with WorkbenchApp(deps_value).run_test(size=(80, 24)) as pilot:
            await pilot.press("c")
            await pilot.pause()
            assert not recorder.purges
            await pilot.press("p")
            await pilot.pause()
            assert not recorder.purges
            await pilot.press("escape")
            await pilot.pause()

    asyncio.run(run())
    assert recorder.purges == []


def test_typed_purge_confirmation_runs_purge(deps):
    deps_value, recorder = deps

    async def run():
        async with WorkbenchApp(deps_value).run_test(size=(80, 24)) as pilot:
            await pilot.press("c")
            await pilot.pause()
            await pilot.press("p")
            await pilot.pause()
            await pilot.press(*"purge")
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(run())
    assert recorder.purges and len(recorder.purges[0]) == 64


def test_delete_requires_the_session_name(deps):
    deps_value, recorder = deps
    app = WorkbenchApp(deps_value)
    app.current = "ses_" + "0" * 32

    async def run():
        async with app.run_test(size=(80, 24)) as pilot:
            app.current = "ses_" + "0" * 32
            await pilot.press("x")
            await pilot.pause()
            await pilot.press(*"wrong")
            await pilot.press("enter")
            await pilot.pause()
            assert recorder.deleted == []
            app.current = "ses_" + "0" * 32
            await pilot.press("x")
            await pilot.pause()
            await pilot.press(*"session-0")
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(run())
    assert recorder.deleted == ["ses_" + "0" * 32]


def test_preview_describes_the_artifact(deps):
    deps_value, _ = deps
    repositories = deps_value.repositories
    store = deps_value.store
    import io

    stream = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(stream, format="PNG")
    identity = "art_" + "b" * 32
    store.stage(identity, stream.getvalue())
    store.publish(identity)
    repositories.insert("artifact_links", {
        "session_id": "ses_" + "0" * 32, "artifact_id": identity, "role": "output",
        "created_at": "2026-10-24T00:00:00Z",
    })
    from imagen_cli.cli.tui import preview

    row = repositories.get("artifacts", identity)
    text = preview.describe(row, store.get(identity))
    assert "4x4" in text
