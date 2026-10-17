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


def _publish_output(deps_value, node: int = 0) -> str:
    import io

    stream = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(stream, format="PNG")
    identity = "art_" + f"{node:032x}"
    deps_value.store.stage(identity, stream.getvalue())
    deps_value.store.publish(identity)
    deps_value.repositories.insert("artifact_links", {
        "session_id": f"ses_{node:032x}", "artifact_id": identity, "role": "output",
        "created_at": "2026-10-24T00:00:00Z",
    })
    return identity


def test_transform_preview_writes_only_on_enter(deps):
    deps_value, _ = deps
    calls = []

    def transform(artifact, pipeline, session):
        calls.append((artifact, pipeline, session))
        return {}

    deps_value.transform = transform

    async def run():
        app = WorkbenchApp(deps_value)
        app.current = "ses_" + "0" * 32
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.press("t")
            await pilot.pause()
            assert calls == []
            await pilot.press("enter")
            await pilot.pause()

    _publish_output(deps_value)
    asyncio.run(run())
    assert len(calls) == 1


def test_providers_refresh_models_uses_the_selection(deps):
    deps_value, _ = deps
    deps_value.repositories.insert("profiles", {
        "id": "p1", "name": "p1", "provider": "openai", "family": "images",
        "base_url": "https://gateway.example/v1", "model_default": "m1",
        "credential_ref": "env:KEY", "trust_policy": {}, "revision": 1,
    })
    refreshed = []

    def refresh(profile):
        refreshed.append(profile)
        return "succeeded", {"models": []}

    deps_value.models_refresh = refresh

    async def run():
        async with WorkbenchApp(deps_value).run_test(size=(100, 30)) as pilot:
            await pilot.press("v")
            await pilot.pause()
            await pilot.press("m")
            await pilot.pause()

    asyncio.run(run())
    assert refreshed == ["p1"]


def test_export_asks_for_a_destination(deps):
    deps_value, _ = deps
    exported = []

    def export(session, destination):
        exported.append((session, destination))
        return "succeeded", {"artifacts": 0}

    deps_value.export = export

    async def run():
        app = WorkbenchApp(deps_value)
        app.current = "ses_" + "0" * 32
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("e")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(run())
    assert exported and exported[0][0] == "ses_" + "0" * 32


def test_new_session_form_creates_and_selects(deps):
    deps_value, _ = deps
    created = []

    def create(name, intent="", profile=None, model=None):
        created.append((name, intent, profile, model))
        return {"id": "ses_" + "f" * 32, "name": name, "version": 0}

    deps_value.create_session = create

    async def run():
        async with WorkbenchApp(deps_value).run_test(size=(100, 30)) as pilot:
            await pilot.press("n")
            await pilot.pause()
            await pilot.press(*"new-session")
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(run())
    assert created == [("new-session", "", None, None)]


def test_generate_plan_requires_approval_before_execution(deps):
    deps_value, _ = deps
    calls = []

    def execute(command, arguments):
        calls.append((command, arguments))
        if command == "generate":
            return "approval_required", {"plan": {
                "id": "pln_" + "1" * 32, "plan_digest": "a" * 64,
                "operation": "generate", "model": "m",
            }}
        if command == "plan approve":
            return "approved", {}
        return "succeeded", {"job": {"id": "job_" + "1" * 32, "state": "succeeded"}}

    deps_value.execute = execute

    async def run():
        app = WorkbenchApp(deps_value)
        app.current = "ses_" + "0" * 32
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("g")
            await pilot.pause()
            await pilot.press(*"a cat")
            await pilot.press("enter")
            await pilot.pause()
            assert [call[0] for call in calls] == ["generate"]
            await pilot.press("a")
            await pilot.pause()
            assert [call[0] for call in calls] == ["generate", "plan approve"]
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(run())
    assert [call[0] for call in calls] == ["generate", "plan approve", "plan execute"]
    assert calls[0][1]["prompt"] == "a cat"


def test_provider_form_creates_a_profile(deps):
    deps_value, _ = deps
    saved = []

    def profile_set(parameters):
        saved.append(parameters)
        return "succeeded", {"profile": {"id": parameters["profile"], "revision": 1}}

    deps_value.profile_set = profile_set

    async def run():
        async with WorkbenchApp(deps_value).run_test(size=(100, 30)) as pilot:
            await pilot.press("v")
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            await pilot.press(*"demo")
            await pilot.press("tab", "tab", "tab")
            await pilot.press(*"https://gateway.example/v1")
            await pilot.press("tab")
            await pilot.press(*"image-model")
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(run())
    assert saved and saved[0]["profile"] == "demo"
    assert saved[0]["base_url"] == "https://gateway.example/v1"
