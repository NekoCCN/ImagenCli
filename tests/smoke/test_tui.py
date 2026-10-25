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

from textual.widgets import Static  # noqa: E402

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


def test_delete_confirmation_cancels_with_escape(deps):
    deps_value, recorder = deps
    app = WorkbenchApp(deps_value)

    async def run():
        async with app.run_test(size=(80, 24)) as pilot:
            app.current = "ses_" + "0" * 32
            await pilot.press("x")
            await pilot.pause()
            assert type(app.screen).__name__ == "ConfirmScreen"
            await pilot.press("escape")
            await pilot.pause()
            assert type(app.screen).__name__ != "ConfirmScreen"

    asyncio.run(run())
    assert recorder.deleted == []


def test_delete_is_hinted_on_screen_and_reported(deps):
    deps_value, recorder = deps

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(80, 24)) as pilot:
            app.current = "ses_" + "0" * 32
            await pilot.pause()
            keybar = " ".join(str(widget.render()) for widget in app.query("#keybar"))
            assert "删除" in keybar
            await pilot.press("x")
            await pilot.pause()
            await pilot.press(*"session-0")
            await pilot.press("enter")
            await pilot.pause()
            assert "已删除" in str(app.status.render())

    asyncio.run(run())
    assert recorder.deleted == ["ses_" + "0" * 32]


def test_new_session_reports_a_duplicate_name(deps):
    deps_value, _ = deps
    import sqlite3

    def create_session(name, intent="", profile=None, model=None):
        raise sqlite3.IntegrityError("UNIQUE constraint failed: sessions.name")

    deps_value.create_session = create_session

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("n")
            await pilot.pause()
            await pilot.press(*"session-0")
            await pilot.press("enter")
            await pilot.pause()
            assert "已存在" in str(app.status.render())

    asyncio.run(run())


def test_keyboard_selection_updates_the_current_session(deps):
    deps_value, _ = deps

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            first = app.current
            await pilot.press("down")
            await pilot.pause()
            assert app.current == "ses_" + f"{1:032x}"
            assert app.current != first

    asyncio.run(run())


def test_refresh_keeps_the_selection_and_status(deps):
    deps_value, _ = deps
    target = "ses_" + f"{42:032x}"

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(80, 24)) as pilot:
            app.current = target
            app.refresh_rows()
            app._say("完成")
            await pilot.pause()
            await pilot.pause()
            assert app.current == target
            assert "完成" in str(app.status.render())

    asyncio.run(run())


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


def test_transform_default_path_publishes_from_isolated_worker(deps):
    from imagen_cli.cli.tui.app import TransformScreen

    deps_value, _ = deps
    _publish_output(deps_value)
    before = len(deps_value.repositories.list("artifacts"))

    async def run():
        app = WorkbenchApp(deps_value)
        app.current = "ses_" + "0" * 32
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("t")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, TransformScreen)
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert not isinstance(app.screen, TransformScreen), str(screen.status.render())

    asyncio.run(run())
    outputs = deps_value.repositories.list("artifacts")
    assert len(outputs) == before + 1
    derived = [row for row in outputs if row["origin_manifest"].get("kind") == "postprocess"]
    assert derived[0]["origin_manifest"]["execution"]["isolation"] == "spawn_process"


def test_transform_escape_cancels_without_publishing(deps, monkeypatch):
    import threading

    from imagen_cli.cli.tui.app import TransformScreen

    deps_value, _ = deps
    _publish_output(deps_value)
    before = len(deps_value.repositories.list("artifacts"))
    started = threading.Event()

    def slow_compute(*args, cancelled, **kwargs):
        started.set()
        assert cancelled.wait(timeout=5)
        raise ValueError("postprocess cancelled")

    monkeypatch.setattr("imagen_cli.application.postprocess_worker.execute", slow_compute)

    async def run():
        app = WorkbenchApp(deps_value)
        app.current = "ses_" + "0" * 32
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("t")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, TransformScreen)
            await pilot.press("enter")
            assert await asyncio.to_thread(started.wait, 2)
            await pilot.press("escape")
            await app.workers.wait_for_complete()
            assert screen.cancelled.is_set()
            assert not screen.running
            assert isinstance(app.screen, TransformScreen)

    asyncio.run(run())
    assert len(deps_value.repositories.list("artifacts")) == before


def test_transform_param_tuning(deps):
    from imagen_cli.cli.tui.app import ParamEditScreen

    deps_value, _ = deps
    calls = []

    def transform(artifact, pipeline, session):
        calls.append((artifact, pipeline, session))
        return {"id": "art_transformed"}

    deps_value.transform = transform

    async def run():
        app = WorkbenchApp(deps_value)
        app.current = "ses_" + "0" * 32
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.press("t")
            await pilot.pause()
            await pilot.press("p")
            await pilot.pause()
            assert isinstance(app.screen, ParamEditScreen)
            await pilot.press("backspace", "8")
            await pilot.press("enter")
            await pilot.pause()
            assert not isinstance(app.screen, ParamEditScreen)
            await pilot.press("enter")
            await pilot.pause()

    _publish_output(deps_value)
    asyncio.run(run())
    assert len(calls) == 1
    pipeline = calls[0][1]
    assert pipeline[0]["params"]["scale"] == 8


def test_param_edit_screen_basic_and_advanced_toggle():
    from textual.app import App, ComposeResult
    from textual.widgets import Static

    from imagen_cli.cli.tui.app import ParamEditScreen

    pipeline = [
        {
            "op": "anime_line_enhance",
            "params": {
                "strength": 1.0,
                "thickness": 1.0,
                "sigma": 1.0,
                "k_ratio": 1.6,
                "tau": 0.98,
                "gamma": 1.5,
                "closing_radius": 1,
            },
        }
    ]

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield Static("test")

    app = TestApp()

    async def run():
        async with app.run_test() as pilot:
            screen = ParamEditScreen("动漫线条增强", pipeline)
            await app.push_screen(screen)
            await pilot.pause()

            # 初始为基础模式，仅显示基础参数
            assert screen.mode == "basic"
            # 基础参数容器可见
            c_strength = screen.query_one("#c_f_0_strength")
            assert c_strength.display is True
            # 高级参数容器被隐藏
            c_sigma = screen.query_one("#c_f_0_sigma")
            assert c_sigma.display is False

            # 按 Tab/m 或触发 action_toggle_mode 切换到高级模式
            screen.action_toggle_mode()
            assert screen.mode == "advanced"
            assert c_sigma.display is True

            # 再次切换回基础模式
            screen.action_toggle_mode()
            assert screen.mode == "basic"
            assert c_sigma.display is False

    asyncio.run(run())


def test_transform_intro_display(deps):
    from imagen_cli.cli.tui.app import TransformScreen

    deps_value, _ = deps

    async def run():
        app = WorkbenchApp(deps_value)
        app.current = "ses_" + "0" * 32
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.press("t")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, TransformScreen)
            intro_text = str(screen.intro_view.render())
            assert "像素化" in intro_text or "Pixelate" in intro_text
            assert "怎么做" in intro_text
            assert "可能带来" in intro_text
            assert "可能失去" in intro_text

            await pilot.press("down")
            await pilot.pause()
            new_intro = str(screen.intro_view.render())
            assert len(new_intro) > 0

            # 按 c 键切换分类筛选
            await pilot.press("c")
            await pilot.pause()
            assert screen.current_category_idx != 0
            assert len(screen.filtered_names) > 0
            cat_intro = str(screen.intro_view.render())
            assert len(cat_intro) > 0

            await pilot.press("escape")
            await pilot.pause()

    _publish_output(deps_value)
    asyncio.run(run())


def test_all_transform_presets_render_successfully(deps):
    from imagen_cli.cli.tui.app import TransformScreen

    deps_value, _ = deps

    async def run():
        app = WorkbenchApp(deps_value)
        app.current = "ses_" + "0" * 32
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.press("t")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, TransformScreen)

            for row_idx in range(len(screen.names)):
                label = str(screen.table.get_cell_at((row_idx, 0)))
                assert label == screen.entries[screen.names[row_idx]]["name"]
                assert not any(char.isascii() and char.isalpha() for char in label)
                screen.table.move_cursor(row=row_idx)
                screen.render_preview()
                view_str = str(screen.view.render())
                intro_str = str(screen.intro_view.render())
                status_str = str(screen.status.render())

                assert "预览不可用" not in view_str, f"Preset {screen.names[row_idx]} render failed"
                assert len(intro_str.strip()) > 0, f"Preset {screen.names[row_idx]} intro empty"
                assert "enter" in status_str

            await pilot.press("escape")
            await pilot.pause()

    _publish_output(deps_value)
    asyncio.run(run())


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


def test_model_picker_sets_the_selected_default(deps):
    deps_value, _ = deps
    deps_value.repositories.insert("profiles", {
        "id": "p1", "name": "p1", "provider": "openai", "family": "images",
        "base_url": "https://gateway.example/v1", "model_default": "m-a",
        "credential_ref": "env:KEY", "trust_policy": {}, "revision": 1,
    })
    for model in ("m-a", "m-b"):
        deps_value.repositories.insert("provider_models", {
            "id": f"p1:{model}", "profile_id": "p1", "model_id": model,
            "display_name": model, "fetched_at": "2026-10-24T00:00:00Z",
            "source_url": "https://gateway.example/v1/models", "evidence": "discovered",
        })
    used = []

    def use(profile, model):
        used.append((profile, model))
        return "succeeded", {"profile": {"id": profile, "revision": 2}}

    deps_value.models_use = use

    async def run():
        async with WorkbenchApp(deps_value).run_test(size=(100, 30)) as pilot:
            await pilot.press("v")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press(*"m-b")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(run())
    assert used == [("p1", "m-b")]


def test_first_session_is_selected_on_mount(deps):
    deps_value, _ = deps

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            visible = app.sessions.coordinate_to_cell_key(
                app.sessions.cursor_coordinate
            ).row_key.value
            assert app.current is not None
            assert app.current == visible

    asyncio.run(run())


def test_generate_defaults_to_the_only_profile(deps):
    deps_value, _ = deps
    from textual.widgets import Input

    deps_value.repositories.insert("profiles", {
        "id": "p1", "name": "p1", "provider": "openai", "family": "images",
        "base_url": "https://gateway.example/v1", "model_default": "picked-model",
        "credential_ref": "env:KEY", "trust_policy": {}, "revision": 1,
    })
    deps_value.execute = lambda command, arguments: ("succeeded", {})
    values = {}

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.press("g")
            await pilot.pause()
            values.update({field.id: field.value for field in app.screen.query(Input)})

    asyncio.run(run())
    assert values["profile"] == "p1"
    assert values["model"] == "picked-model"


def test_consumed_plan_shows_its_result_instead_of_crashing(deps):
    deps_value, _ = deps
    from imagen_cli.application.planning_service import PlanningService
    from imagen_cli.cli.tui.app import PlanScreen
    from imagen_cli.domain.primitive import new_id

    session_id = new_id("ses")
    deps_value.repositories.insert("sessions", {
        "id": session_id, "name": "work", "version": 0,
        "created_at": "2026-10-24T00:00:00Z",
    })
    plan_row, approval = PlanningService(deps_value.repositories).create_plan(
        session_id=session_id,
        target_snapshot={
            "profile_identity": "offline", "profile_revision": 1,
            "endpoint_origin": "https://example.test",
            "allowed_upload_targets": ["https://example.test"],
            "provider": "openai", "family": "images", "model_revision": "local",
        },
        operation="generate", model="picked-model",
        normalized_parameters={"count": 1}, prompt_snapshot="cat",
        input_hashes=[], portable_context={"kind": "none"}, capability_digest="a" * 64,
    )
    deps_value.repositories.update("approvals", approval.id, {"state": "consumed"})
    plan = plan_row.model_dump(mode="json")
    calls = []

    def execute(command, arguments):
        calls.append(command)
        return "succeeded", {"job": {"id": "job_" + "1" * 32, "state": "succeeded"}}

    deps_value.execute = execute
    app = WorkbenchApp(deps_value)

    async def run():
        async with app.run_test(size=(100, 30)) as pilot:
            app.push_screen(PlanScreen(deps_value, plan))
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            assert "plan approve" not in calls
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert not isinstance(app.screen, PlanScreen)

    asyncio.run(run())
    assert calls == ["plan execute"]


def test_approve_error_is_shown_instead_of_raising(deps):
    deps_value, _ = deps
    from imagen_cli.cli.tui.app import PlanScreen

    deps_value.execute = lambda command, arguments: (_ for _ in ()).throw(ValueError("boom"))
    app = WorkbenchApp(deps_value)
    plan = {"id": "pln_" + "1" * 32, "plan_digest": "a" * 64,
            "operation": "generate", "model": "picked-model"}

    async def run():
        async with app.run_test(size=(100, 30)) as pilot:
            app.push_screen(PlanScreen(deps_value, plan))
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            assert isinstance(app.screen, PlanScreen)

    asyncio.run(run())


def test_retry_key_rebuilds_the_request(deps):
    deps_value, _ = deps
    from imagen_cli.cli.tui.app import PlanScreen

    calls = []

    def execute(command, arguments):
        calls.append(command)
        return "approval_required", {"plan": {
            "id": "pln_" + "2" * 32, "plan_digest": "b" * 64,
            "operation": "generate", "model": "picked-model",
        }}

    deps_value.execute = execute
    app = WorkbenchApp(deps_value)
    plan = {"id": "pln_" + "1" * 32, "plan_digest": "a" * 64,
            "operation": "generate", "model": "picked-model"}

    async def run():
        async with app.run_test(size=(100, 30)) as pilot:
            app.push_screen(PlanScreen(deps_value, plan))
            await pilot.pause()
            await pilot.press("r")
            await pilot.pause()
            assert isinstance(app.screen, PlanScreen)
            assert app.screen.plan["id"] == "pln_" + "2" * 32

    asyncio.run(run())
    assert calls == ["plan retry"]


def test_open_keys_open_the_current_image_and_folder(deps):
    deps_value, _ = deps
    identity = _publish_output(deps_value)
    opened = []

    def open_artifact(artifact, folder):
        opened.append((artifact, folder))
        return "succeeded", {"path": "/tmp/artifact"}

    deps_value.open_artifact = open_artifact

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.press("o")
            await pilot.pause()
            await pilot.press("O")
            await pilot.pause()

    asyncio.run(run())
    assert opened == [(identity, False), (identity, True)]


def test_help_lists_the_delete_and_open_keys(deps):
    deps_value, _ = deps
    from textual.widgets import Static

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("?")
            await pilot.pause()
            assert type(app.screen).__name__ == "HelpScreen"
            text = " ".join(str(widget.render()) for widget in app.screen.query(Static))
            assert "x" in text and "删除会话" in text
            assert "打开图片" in text and "打开目录" in text

    asyncio.run(run())


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
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert [call[0] for call in calls] == ["generate"]
            await pilot.press("a")
            await pilot.pause()
            assert [call[0] for call in calls] == ["generate", "plan approve"]
            await pilot.press("enter")
            await app.workers.wait_for_complete()
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


def test_command_palette_runs_commands_without_fields(deps):
    deps_value, _ = deps
    calls = []

    def execute(command, arguments):
        calls.append((command, arguments))
        return "succeeded", {"operations": ["pixelate"]}

    deps_value.execute = execute

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app._open_command("ops")
            await app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(run())
    assert calls == [("ops", {})]


def test_update_form_prefills_the_selected_profile(deps):
    deps_value, _ = deps
    deps_value.repositories.insert("profiles", {
        "id": "p1", "name": "p1", "provider": "openai", "family": "images",
        "base_url": "https://gateway.example/v1", "model_default": "m1",
        "credential_ref": "env:KEY", "trust_policy": {}, "revision": 3,
    })
    saved = []

    def profile_set(parameters):
        saved.append(parameters)
        return "succeeded", {"profile": {"id": "p1", "revision": 4}}

    deps_value.profile_set = profile_set

    async def run():
        async with WorkbenchApp(deps_value).run_test(size=(100, 30)) as pilot:
            await pilot.press("v")
            await pilot.pause()
            await pilot.press("u")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(run())
    assert saved == [{
        "profile": "p1", "provider": "openai", "family": "images",
        "base_url": "https://gateway.example/v1", "model_default": "m1",
        "credential_ref": "env:KEY", "revision": 3,
    }]


def test_provider_form_accepts_a_direct_api_key(deps):
    deps_value, _ = deps
    saved = []
    secrets = []

    def secret_ref(profile, key):
        secrets.append((profile, key))
        return "private:cred-demo"

    def profile_set(parameters):
        saved.append(parameters)
        return "succeeded", {"profile": {"id": parameters["profile"], "revision": 1}}

    deps_value.secret_ref = secret_ref
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
            await pilot.press("tab", "tab")
            await pilot.press(*"sk-direct")
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(run())
    assert secrets == [("demo", "sk-direct")]
    assert saved and saved[0]["credential_ref"] == "private:cred-demo"
    assert "api_key" not in saved[0]


def _link_output(deps_value, node: int, colour: str) -> str:
    import io

    stream = io.BytesIO()
    Image.new("RGB", (8, 8), colour).save(stream, format="PNG")
    identity = "art_" + f"{node:032x}"
    deps_value.store.stage(identity, stream.getvalue())
    deps_value.store.publish(identity)
    deps_value.repositories.insert("artifact_links", {
        "session_id": "ses_" + "0" * 32, "artifact_id": identity, "role": "output",
        "created_at": "2026-10-24T00:00:00Z",
    })
    return identity


def test_filmstrip_switches_the_preview(deps):
    deps_value, _ = deps
    identities = [_link_output(deps_value, 11, "red"), _link_output(deps_value, 12, "blue")]
    app = WorkbenchApp(deps_value)

    async def run():
        async with app.run_test(size=(80, 24)) as pilot:
            app.current = "ses_" + "0" * 32
            app.refresh_rows()
            await pilot.pause()
            assert [item["id"] for item in app.film_items] == identities
            assert app.film_index == 1
            await pilot.press("[")
            await pilot.pause()
            assert app.film_index == 0
            found = app._current_artifact()
            assert found is not None and found[0] == identities[0]
            await pilot.press("]")
            await pilot.pause()
            assert app.film_index == 1

    asyncio.run(run())


def test_command_palette_filters_by_name(deps):
    deps_value, _ = deps
    calls = []

    def execute(command, arguments):
        calls.append(command)
        return "succeeded", {"operations": ["pixelate"]}

    deps_value.execute = execute

    async def run():
        from imagen_cli.cli.tui.app import CommandPaletteScreen

        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("colon")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, CommandPaletteScreen)
            total = len(screen.matches)
            await pilot.press(*"ops")
            await pilot.pause()
            assert 0 < len(screen.matches) <= total
            assert any(name == "ops" for name in screen.matches)
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(run())
    assert calls == ["ops"]


def test_plan_execution_is_async_and_cannot_be_dismissed(deps):
    import threading

    from imagen_cli.cli.tui.app import PlanScreen

    deps_value, _ = deps
    started = threading.Event()
    gate = threading.Event()

    def execute(command, arguments):
        if command == "plan execute":
            started.set()
            gate.wait(5)
            return "succeeded", {"job": {"id": "job_" + "1" * 32, "state": "succeeded"}}
        return "approved", {}

    deps_value.execute = execute
    app = WorkbenchApp(deps_value)
    plan = {"id": "pln_" + "1" * 32, "plan_digest": "a" * 64,
            "operation": "generate", "model": "picked-model"}

    async def run():
        async with app.run_test(size=(100, 30)) as pilot:
            app.push_screen(PlanScreen(deps_value, plan))
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert started.wait(5)
            assert isinstance(app.screen, PlanScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, PlanScreen)
            gate.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert not isinstance(app.screen, PlanScreen)

    asyncio.run(run())


def test_worker_executor_uses_an_isolated_workspace(tmp_path):
    from imagen_cli.cli.tui.app import _executor

    root = tmp_path / "ws"
    root.mkdir()
    repositories = Repositories(connect(root / "state.sqlite3"))
    store = ArtifactStore(root / "artifacts", repositories)
    deps = Deps(
        repositories, store,
        execute=lambda command, arguments: (_ for _ in ()).throw(AssertionError("main")),
        workspace_root=root,
    )
    try:
        status, data = _executor(deps)("ops", {})
    finally:
        repositories.connection.close()
    assert status == "succeeded"
    assert "pixelate" in data["operations"]


def test_tui_delete_artifact_key_binding(deps):
    import io

    from imagen_cli.cli.tui.app import ConfirmScreen

    deps_value, _ = deps
    repositories = deps_value.repositories
    store = deps_value.store
    session_id = "ses_00000000000000000000000000000000"
    art_id = "art_" + "d" * 32

    img = Image.new("RGB", (16, 16), (200, 100, 100))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw = buf.getvalue()

    store.stage(art_id, raw)
    store.publish(art_id)
    repositories.insert("artifact_links", {
        "session_id": session_id,
        "artifact_id": art_id,
        "role": "output",
        "created_at": "2026-10-24T00:00:00Z",
    })

    app = WorkbenchApp(deps_value)

    async def run():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            # Select first session
            app.current = session_id
            app.show_detail()
            await pilot.pause()
            assert len(app.film_items) >= 1
            assert str(app.film_items[0]["id"]) == art_id

            # Press 'd' to open ConfirmScreen
            await pilot.press("d")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)

            # Type 'yes' and enter to confirm removal
            for ch in "yes":
                await pilot.press(ch)
            await pilot.press("enter")
            await pilot.pause()

            # ConfirmScreen dismissed, film_items empty
            assert not isinstance(app.screen, ConfirmScreen)
            assert len(app.film_items) == 0
            # Database link removed
            links = repositories.list("artifact_links", session_id=session_id, artifact_id=art_id)
            assert len(links) == 0

    asyncio.run(run())



def test_toggle_language_updates_visible_labels(deps):
    deps_value, _ = deps
    app = WorkbenchApp(deps_value)

    async def run():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("l")
            await pilot.pause()
            assert "Language: English" in str(app.status.render())
            assert "Generate" in str(app.query_one("#keybar", Static).render())
            await pilot.press("l")
            await pilot.pause()
            assert "语言：中文" in str(app.status.render())
            assert "生成" in str(app.query_one("#keybar", Static).render())

    asyncio.run(run())
