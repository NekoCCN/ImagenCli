"""TUI pilot: read-only browsing, confirmation dialogs, no writes before consent."""

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


def test_workbench_exposes_primary_actions_as_focusable_buttons(deps):
    deps_value, _ = deps

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            ids = {str(button.id) for button in app.query("#action-bar Button")}
            assert {"action-generate", "action-edit", "action-transform", "action-open"} <= ids
            await pilot.press("q")

    asyncio.run(run())


def test_mouse_actions_keep_confirmation_and_compact_layout(deps):
    from textual.widgets import Button

    deps_value, recorder = deps

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert app.screen.has_class("compact")
            assert app.query_one("#action-edit", Button).disabled
            await pilot.click("#action-providers")
            await pilot.pause()
            await pilot.click("#providers-add")
            await pilot.pause()
            assert type(app.screen).__name__ == "ProviderForm"
            await pilot.press("escape", "escape")
            await pilot.click("#action-delete")
            await pilot.pause()
            assert type(app.screen).__name__ == "ConfirmScreen"
            assert not recorder.deleted
            await pilot.click("#confirm-no")
            await pilot.pause()
            assert not recorder.deleted
            await pilot.resize_terminal(140, 40)
            await pilot.pause()
            assert not app.screen.has_class("compact")

    asyncio.run(run())


def test_plan_mouse_actions_preserve_approval_boundary(deps):
    from imagen_cli.cli.tui.app import PlanScreen

    deps_value, _ = deps
    calls = []

    def execute(command, arguments):
        calls.append(command)
        return "approved", {}

    deps_value.execute = execute

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(80, 24)) as pilot:
            await app.push_screen(PlanScreen(deps_value, {"id": "plan-test", "plan_digest": "abc"}))
            await pilot.pause()
            from textual.widgets import Button

            assert app.screen.query_one("#plan-execute", Button).disabled
            await pilot.click("#plan-approve")
            await pilot.pause()
            assert calls == ["plan approve"]
            assert not app.screen.query_one("#plan-execute", Button).disabled
            await pilot.click("#plan-cancel")
            await pilot.pause()
            assert calls == ["plan approve"]

    asyncio.run(run())


def test_form_mouse_submit_validates_before_dismissal(deps):
    from textual.widgets import Input

    from imagen_cli.cli.tui.app import FormScreen

    deps_value, _ = deps
    results = []

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(80, 24)) as pilot:
            await app.push_screen(FormScreen("Test", [("name", "name", "")]), results.append)
            await pilot.click("#form-submit")
            await pilot.pause()
            assert isinstance(app.screen, FormScreen)
            assert not results
            app.screen.query_one(Input).value = "session"
            await pilot.pause(0.4)
            await pilot.click("#form-submit")
            await pilot.pause()
            assert results == [{"name": "session"}]

    asyncio.run(run())


def test_cached_or_empty_preview_rejects_stale_worker_result(deps):
    deps_value, _ = deps

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test() as pilot:
            await pilot.pause()
            old_request = app._preview_request_id
            app._render_preview()
            before = str(app.preview.render())
            app._on_preview_loaded(old_request, "stale", Path("old.png"), 10, 10, 0.0)
            assert str(app.preview.render()) == before

    asyncio.run(run())


def test_preview_failure_displays_fallback_without_accepting_stale_errors(deps):
    deps_value, _ = deps

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test() as pilot:
            await pilot.pause()
            request = app._preview_request_id
            app._on_preview_failed(request, "broken.png")
            message = str(app.preview.render())
            assert "broken.png" in message
            app._on_preview_failed(request - 1, "stale.png")
            assert str(app.preview.render()) == message

    asyncio.run(run())


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


def test_delete_confirmation_defaults_to_no_and_accepts_yes(deps):
    deps_value, recorder = deps
    app = WorkbenchApp(deps_value)

    async def run():
        async with app.run_test(size=(80, 24)) as pilot:
            app.current = "ses_" + "0" * 32
            await pilot.press("x")
            await pilot.pause()
            await pilot.press("enter")  # No is selected by default
            await pilot.pause()
            assert recorder.deleted == []
            app.current = "ses_" + "0" * 32
            await pilot.press("x")
            await pilot.pause()
            await pilot.press("right")
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
            await pilot.press("y")
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
            assert "操作：" in intro_text
            assert "可能的优势：" in intro_text
            assert "可能的缺点：" in intro_text
            assert "可能用来：" in intro_text

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
            values.update(app.screen.values())

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


def test_new_session_cancel_survives_a_second_activation(deps):
    """Two activations for one intent must not pop the screen underneath."""
    from textual.widgets import Button

    deps_value, _ = deps
    app = WorkbenchApp(deps_value)

    async def run():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("n")
            await pilot.pause()
            assert type(app.screen).__name__ == "CreationForm"
            cancel = app.screen.query_one("#cancel", Button)
            cancel.press()
            cancel.press()
            await pilot.pause()
            assert [type(screen).__name__ for screen in app.screen_stack] == ["Screen"]

    asyncio.run(run())


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
            from textual.widgets import Input, Select

            screen = pilot.app.screen
            screen.query_one("#profile", Input).value = "demo"
            screen.query_one("#base_url", Input).value = "https://gateway.example/v1"
            screen.query_one("#model_default", Input).value = "image-model"
            screen.query_one("#credential_mode", Select).value = "env"
            await pilot.pause()
            screen.query_one("#credential_ref", Input).value = "KEY"
            screen.submit()
            await pilot.pause()

    asyncio.run(run())
    assert saved and saved[0]["profile"] == "demo"
    assert saved[0]["base_url"] == "https://gateway.example/v1"


def test_worker_start_asks_before_consuming_the_queue(deps):
    deps_value, _ = deps
    calls = []

    def execute(command, arguments):
        calls.append(command)
        return "succeeded", {}

    deps_value.execute = execute

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app._open_command("worker start")
            await pilot.pause()
            assert type(app.screen).__name__ == "ConfirmScreen"
            await pilot.press("enter")  # No by default
            await pilot.pause()
            assert calls == []
            app._open_command("worker start")
            await pilot.pause()
            await pilot.press("y")
            await app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(run())
    assert calls == ["worker start"]


def test_command_palette_shows_a_form_even_when_nothing_is_required(deps):
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
            await pilot.pause()
            await pilot.press("enter")  # submit the all-optional form
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
        "models_path": "/v1/models", "credential_ref": "env:KEY", "revision": 3,
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
            from textual.widgets import Input

            screen = pilot.app.screen
            screen.query_one("#profile", Input).value = "demo"
            screen.query_one("#base_url", Input).value = "https://gateway.example/v1"
            screen.query_one("#model_default", Input).value = "image-model"
            screen.query_one("#api_key", Input).value = "sk-direct"
            screen.submit()
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
            await pilot.pause()
            await pilot.press("enter")  # the palette now opens a form; submit it
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

            # Move to Yes and confirm removal
            await pilot.press("right")
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


def test_toggle_language_persists_choice(deps):
    deps_value, _ = deps
    app = WorkbenchApp(deps_value)

    async def run():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("l")
            await pilot.pause()

    asyncio.run(run())
    from imagen_cli import i18n

    i18n.reset_language()
    assert i18n.saved_language() == "en"


def _empty_deps(tmp_path: Path):
    root = tmp_path / "empty-workspace"
    root.mkdir()
    repositories = Repositories(connect(root / "state.sqlite3"))
    store = ArtifactStore(root / "artifacts", repositories)
    return Deps(repositories, store, lambda *a, **k: [], lambda *a, **k: {}, lambda *a, **k: {})


def test_session_columns_fit_the_sidebar_at_every_width(deps):
    """The last header must stay on screen; long names truncate instead."""
    deps_value, _ = deps

    async def run():
        for size in ((80, 24), (100, 30), (140, 40)):
            app = WorkbenchApp(deps_value)
            async with app.run_test(size=size) as pilot:
                await pilot.pause()
                table = app.sessions
                total = sum(column.get_render_width(table) for column in table.columns.values())
                assert total <= table.size.width, (size, total, table.size.width)
                await pilot.press("q")

    asyncio.run(run())


def test_detail_shows_the_endpoint_name_not_its_id(deps):
    deps_value, _ = deps
    repositories = deps_value.repositories
    identity = "ses_" + "e" * 32
    repositories.insert("sessions", {
        "id": identity, "name": "cel-poster", "version": 1,
        "created_at": "2026-10-24T00:00:00Z",
    })
    repositories.insert("profiles", {
        "id": "prf_" + "a" * 32, "name": "local-openai", "provider": "openai",
        "family": "images", "base_url": "https://example.test", "revision": 1,
        "model_default": "flux-dev", "credential_ref": "private-reference", "trust_policy": {},
    })
    repositories.update("sessions", identity, {
        "default_profile_id": "prf_" + "a" * 32, "default_model": "flux-dev",
    })

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(100, 30)) as pilot:
            app.current = identity
            app.show_detail()
            await pilot.pause()
            meta = str(app.meta.render())
            assert "local-openai" in meta
            assert "prf_" not in meta

    asyncio.run(run())


def test_keybar_keeps_the_common_keys_and_help_at_80_columns(deps):
    """The legend must not silently drop the keys a narrow terminal needs most."""
    from rich.cells import cell_len

    deps_value, _ = deps

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(80, 24)) as pilot:
            app.current = "ses_" + "0" * 32
            await pilot.pause()
            widget = app.query_one("#keybar", Static)
            line = str(widget.render())
            for key in ("g", "m", "t", "d", "x", "删除", ":", "?"):
                assert key in line, (key, line)
            assert cell_len(line) <= widget.content_size.width

    asyncio.run(run())


def test_keybar_reveals_more_keys_on_a_wider_terminal(deps):
    deps_value, _ = deps

    async def read(size):
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=size) as pilot:
            app.current = "ses_" + "0" * 32
            await pilot.pause()
            return str(app.query_one("#keybar", Static).render())

    narrow = asyncio.run(read((80, 24)))
    wide = asyncio.run(read((140, 40)))
    assert "端点" in wide and "产物" in wide and "导出" in wide
    assert len(wide) > len(narrow)


def test_action_bar_pins_a_compact_pager_to_the_right(deps):
    deps_value, _ = deps
    from textual.widgets import Button

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            previous = app.query_one("#action-prev", Button)
            following = app.query_one("#action-next", Button)
            generate = app.query_one("#action-generate", Button)
            bar = app.query_one("#action-bar")
            assert previous.region.width <= 4 and following.region.width <= 4
            assert generate.region.width < bar.region.width // 3
            assert following.region.right <= bar.region.right

    asyncio.run(run())


def test_empty_workspace_gives_the_new_session_hint_once(tmp_path):
    """The preview carries the empty-state instruction; the status line stays quiet."""
    deps_value = _empty_deps(tmp_path)

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            preview = str(app.query_one("#preview").render())
            status = str(app.status.render())
            assert "新建" in preview
            assert "新建" not in status

    asyncio.run(run())


def test_long_session_names_truncate_with_an_ellipsis(deps):
    deps_value, _ = deps
    repositories = deps_value.repositories
    identity = "ses_" + "f" * 32
    repositories.insert("sessions", {
        "id": identity, "name": "anime-cel-chapter-12-final", "version": 7,
        "created_at": "2026-10-24T00:00:00Z",
    })

    async def run():
        from rich.cells import cell_len

        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            name_column = next(iter(app.sessions.columns.values()))
            cell = str(app.sessions.get_row(identity)[0])
            assert cell.endswith("…")
            assert cell_len(cell) <= name_column.width

    asyncio.run(run())


def test_help_shows_every_shortcut_without_scrolling_at_80x24(deps):
    """A key list that scrolls hides exactly the keys people cannot guess."""
    deps_value, _ = deps

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("?")
            await pilot.pause()
            visible = "\n".join(
                "".join(segment.text for segment in strip)
                for strip in app.screen._compositor.render_strips()
            )
            for label in ("退出", "缓存", "端点", "新建会话", "帮助"):
                assert label in visible, label

    asyncio.run(run())


def test_palette_superres_fetch_offers_the_cli_weights_catalog(deps):
    """The CLI picker and the TUI form must read one catalog, not two."""
    from textual.widgets import Select

    from imagen_cli.application.superres import fetch_choices

    deps_value, _ = deps
    deps_value.execute = lambda command, arguments: ("succeeded", {})

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(120, 40)) as pilot:
            app._open_command("superres fetch")
            await pilot.pause()
            selector = app.screen.query_one("#model", Select)
            shown = [
                str(prompt) for prompt, value in selector._options if value is not Select.NULL
            ]
            assert shown == [label for label, _ in fetch_choices()]
            assert any("x |" in label for label in shown)

    asyncio.run(run())


def test_palette_skills_install_offers_the_cli_target_checklist(deps):
    from textual.widgets import SelectionList

    from imagen_cli.application.skill_targets import prompt_order

    deps_value, _ = deps
    deps_value.execute = lambda command, arguments: ("succeeded", {})
    labels, preselected = prompt_order()

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(120, 40)) as pilot:
            app._open_command("skills install")
            await pilot.pause()
            listing = app.screen.query_one("#target", SelectionList)
            prompts = [option.prompt.plain for option in listing._options]
            assert [prompt[4:] for prompt in prompts] == labels
            assert [prompt[:3] for prompt in prompts] == [
                "[x]" if label in preselected else "[ ]" for label in labels
            ]
            assert app.screen.values()["target"] == ",".join(preselected)

    asyncio.run(run())


def test_palette_offers_known_profiles_but_creation_still_types_its_own(deps):
    from textual.widgets import Input, Select

    deps_value, _ = deps
    deps_value.execute = lambda command, arguments: ("succeeded", {})
    repositories = deps_value.repositories
    repositories.insert("profiles", {
        "id": "prf_" + "a" * 32, "name": "local-openai", "provider": "openai",
        "family": "images", "base_url": "https://example.test", "revision": 1,
        "model_default": "flux-dev", "credential_ref": "private-reference", "trust_policy": {},
    })
    identity = "prf_" + "a" * 32

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(120, 40)) as pilot:
            app._open_command("models list")
            await pilot.pause()
            options = [value for _, value in app.screen.query_one("#profile", Select)._options]
            assert identity in options
            await pilot.press("escape")
            await pilot.pause()
            app._open_command("profile add")
            await pilot.pause()
            # A new id cannot be picked from the existing ones, so this one stays typeable.
            assert app.screen.query_one("#profile", Input)

    asyncio.run(run())


def test_palette_skills_targets_submit_as_the_cli_flags(deps):
    """Ticking the checklist must produce the arguments the CLI picker would build."""
    from textual.widgets import Button, SelectionList

    from imagen_cli.application.skill_targets import prompt_order

    deps_value, _ = deps
    calls = []

    def execute(command, arguments):
        calls.append((command, arguments))
        return "succeeded", {}

    deps_value.execute = execute
    _, preselected = prompt_order()

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(120, 40)) as pilot:
            app._open_command("skills install")
            await pilot.pause()
            listing = app.screen.query_one("#target", SelectionList)
            assert listing.selected == preselected
            listing.deselect("claude-code")
            await pilot.pause()
            app.screen.query_one("#submit", Button).press()
            await app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(run())
    targets = [name for name in preselected if name != "claude-code"]
    assert calls == [
        ("skills install", {"force": False, "scope": "project", "target": targets})
    ]


def _superres_workspace(monkeypatch, tmp_path):
    """Weights live here, so the catalog reports a real download state."""
    from imagen_cli.application import superres

    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    target = superres.weights_path("film-degrainer")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"weights")
    return superres


def test_superres_screen_lists_the_cli_catalog(deps, monkeypatch, tmp_path):
    """The panel reads the same catalog the CLI prints, download state included."""
    from textual.widgets import DataTable

    _superres_workspace(monkeypatch, tmp_path)
    from imagen_cli.application.superres import MANIFEST, catalog

    deps_value, _ = deps
    expected = catalog()["models"]

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            assert type(app.screen).__name__ == "SuperresScreen"
            table = app.screen.query_one("#superres-table", DataTable)
            assert table.row_count == len(MANIFEST)
            rows = [
                [str(cell) for cell in table.get_row_at(index)]
                for index in range(table.row_count)
            ]
            assert [row[0] for row in rows] == [row["model"] for row in expected]
            downloaded = {row["model"]: row["downloaded"] for row in expected}
            for row in rows:
                assert ("已下载" in row[-1]) is downloaded[row[0]]

    asyncio.run(run())


def test_superres_screen_asks_before_downloading(deps, monkeypatch, tmp_path):
    module = _superres_workspace(monkeypatch, tmp_path)
    from textual.widgets import DataTable

    from imagen_cli.application.superres import catalog

    deps_value, _ = deps
    calls = []
    missing = [index for index, row in enumerate(catalog()["models"]) if not row["downloaded"]]
    index = missing[0]
    chosen = catalog()["models"][index]["model"]

    def execute(command, arguments):
        calls.append((command, arguments))
        target = module.weights_path(arguments["model"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"weights")
        return "succeeded", {}

    deps_value.execute = execute

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            table = app.screen.query_one("#superres-table", DataTable)
            table.move_cursor(row=index)
            await pilot.pause()
            assert "未下载" in table.get_row_at(index)[-1]
            await pilot.press("f")
            await pilot.pause()
            assert type(app.screen).__name__ == "ConfirmScreen"
            assert calls == []  # consent comes before any download
            await pilot.press("y")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert calls == [("superres fetch", {"model": chosen, "accept": True})]
            assert type(app.screen).__name__ == "ResultScreen"
            app.pop_screen()
            await pilot.pause()
            assert type(app.screen).__name__ == "SuperresScreen"
            assert "已下载" in app.screen.query_one(
                "#superres-table", DataTable
            ).get_row_at(index)[-1]

    asyncio.run(run())


def test_superres_screen_installs_the_runtime_after_consent(deps, monkeypatch, tmp_path):
    _superres_workspace(monkeypatch, tmp_path)

    deps_value, _ = deps
    calls = []

    def execute(command, arguments):
        calls.append((command, arguments))
        return "succeeded", {}

    deps_value.execute = execute

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            await pilot.press("i")
            await pilot.pause()
            assert type(app.screen).__name__ == "ConfirmScreen"
            assert calls == []
            await pilot.press("y")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert calls == [("superres install", {"accept": True})]

    asyncio.run(run())


def test_superres_screen_is_listed_in_the_keybar_and_help(deps):
    deps_value, _ = deps
    deps_value.execute = lambda command, arguments: ("succeeded", {})

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            assert "s  本地模型" in str(app.query_one("#keybar", Static).render())
            await pilot.press("?")
            await pilot.pause()
            assert ("s", "本地模型") in app.screen.rows
            visible = "\n".join(
                "".join(segment.text for segment in strip)
                for strip in app.screen._compositor.render_strips()
            )
            assert "本地模型" in visible

    asyncio.run(run())


def _option_text(option) -> str:
    label = option[0]
    return label.plain if hasattr(label, "plain") else str(label)


def test_palette_session_field_offers_workspace_sessions_and_keeps_typing(deps):
    """The CLI takes a session id; the palette offers the ones this workspace has."""
    from textual.widgets import Input, Select

    deps_value, _ = deps
    deps_value.execute = lambda command, arguments: ("succeeded", {})

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._open_command("session show")
            await pilot.pause()
            form = app.screen
            selector = form.query_one("#session-choice", Select)
            values = [value for _, value in selector._options if value is not Select.NULL]
            assert f"ses_{0:032x}" in values
            assert f"ses_{99:032x}" in values
            assert any("session-0 (" in _option_text(option) for option in selector._options)
            # Nothing picked yet: the list shows "custom" and the field is typeable.
            assert form.query_one("#session", Input).display
            # Picking carries the id into the field the command reads.
            selector.value = f"ses_{7:032x}"
            await pilot.pause()
            assert form.values()["session"] == f"ses_{7:032x}"
            assert not form.query_one("#session", Input).display
            # ...and typing an id the workspace never saw still works.
            selector.value = "custom"
            await pilot.pause()
            assert form.query_one("#session", Input).display
            typed = "ses_" + "f" * 32
            form.query_one("#session", Input).value = typed
            assert form.values()["session"] == typed

    asyncio.run(run())


def test_palette_artifact_field_offers_the_current_session_artifacts(deps):
    from io import BytesIO

    from textual.widgets import Input, Select

    deps_value, _ = deps
    deps_value.execute = lambda command, arguments: ("succeeded", {})
    identities = []
    for seed, colour in (("a", "red"), ("b", "blue")):
        identity = "art_" + seed * 32
        buffer = BytesIO()
        Image.new("RGB", (8, 8), colour).save(buffer, format="PNG")
        deps_value.store.stage(identity, buffer.getvalue())
        deps_value.store.publish(identity)
        deps_value.repositories.insert("artifact_links", {
            "session_id": f"ses_{0:032x}", "artifact_id": identity, "role": "output",
            "created_at": "2026-10-24T00:00:00Z",
        })
        identities.append(identity)

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._open_command("asset show")
            await pilot.pause()
            form = app.screen
            selector = form.query_one("#artifact-choice", Select)
            values = [value for _, value in selector._options if value is not Select.NULL]
            assert set(identities) <= set(values)
            assert isinstance(form.query_one("#artifact", Input), Input)

    asyncio.run(run())


def test_palette_fields_stay_typeable_when_there_is_nothing_to_offer(tmp_path):
    """An empty workspace must not turn a field into a dropdown with no options."""
    from textual.widgets import Input

    deps_value = _empty_deps(tmp_path)
    deps_value.execute = lambda command, arguments: ("succeeded", {})

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._open_command("session show")
            await pilot.pause()
            assert isinstance(app.screen.query_one("#session", Input), Input)
            await pilot.press("escape")
            await pilot.pause()
            app._open_command("asset show")
            await pilot.pause()
            assert isinstance(app.screen.query_one("#artifact", Input), Input)
            assert not list(app.screen.query("#artifact-choice"))

    asyncio.run(run())


def test_superres_state_column_stays_on_screen(deps, monkeypatch, tmp_path):
    """Same rule as the session table: truncate a cell, never push the last column away."""
    from textual.widgets import DataTable

    _superres_workspace(monkeypatch, tmp_path)
    deps_value, _ = deps
    deps_value.execute = lambda command, arguments: ("succeeded", {})

    async def run():
        for size in ((80, 24), (140, 40)):
            app = WorkbenchApp(deps_value)
            async with app.run_test(size=size) as pilot:
                await pilot.pause()
                await pilot.press("s")
                await pilot.pause()
                table = app.screen.query_one("#superres-table", DataTable)
                panel = app.screen.query_one("#superres")
                total = sum(
                    column.get_render_width(table) for column in table.columns.values()
                )
                assert total <= panel.content_size.width
                visible = "\n".join(
                    "".join(segment.text for segment in strip)
                    for strip in app.screen._compositor.render_strips()
                )
                assert "未下载" in visible

    asyncio.run(run())


def test_palette_models_use_follows_the_chosen_profile(deps):
    """`models use --id` offers that profile's declared models, and still accepts typing."""
    from textual.widgets import Input, Select

    deps_value, _ = deps
    deps_value.execute = lambda command, arguments: ("succeeded", {})
    repositories = deps_value.repositories
    identities = {}
    for seed, models in (("a", ["flux-dev"]), ("b", ["sdxl", "sdxl-turbo"])):
        identity = "prf_" + seed * 32
        identities[seed] = identity
        repositories.insert("profiles", {
            "id": identity, "name": f"profile-{seed}", "provider": "openai",
            "family": "images", "base_url": "https://example.test", "revision": 1,
            "model_default": models[0], "credential_ref": "private-reference",
            "trust_policy": {},
        })
        for model in models:
            repositories.insert("provider_models", {
                "id": f"{identity}:{model}", "profile_id": identity, "model_id": model,
                "display_name": model, "fetched_at": "2026-10-24T00:00:00Z",
                "source_url": "https://example.test/v1/models", "evidence": "discovered",
            })

    def options(form):
        selector = form.query_one("#id-choice", Select)
        return [value for _, value in selector._options if value is not Select.NULL]

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._open_command("models use")
            await pilot.pause()
            form = app.screen
            assert isinstance(form.query_one("#id", Input), Input)
            form.query_one("#profile", Select).value = identities["a"]
            await pilot.pause()
            assert options(form) == ["flux-dev", "custom"]
            # Switching profile replaces the list instead of adding to it.
            form.query_one("#profile", Select).value = identities["b"]
            await pilot.pause()
            assert options(form) == ["sdxl", "sdxl-turbo", "custom"]
            form.query_one("#id-choice", Select).value = "sdxl-turbo"
            await pilot.pause()
            assert form.values()["id"] == "sdxl-turbo"
            # `models use` registers an undeclared id too, so typing must survive.
            form.query_one("#id-choice", Select).value = "custom"
            await pilot.pause()
            form.query_one("#id", Input).value = "not-declared-yet"
            assert form.values()["id"] == "not-declared-yet"

    asyncio.run(run())


def test_checklist_tick_text_follows_the_toggle(deps):
    """Textual draws one glyph for both states, so the [x]/[ ] label carries the state."""
    from textual.widgets import SelectionList

    deps_value, _ = deps
    deps_value.execute = lambda command, arguments: ("succeeded", {})

    def tick(lister, index):
        return str(lister.options[index].prompt)[:3]

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._open_command("skills install")
            await pilot.pause()
            lister = app.screen.query_one("#target", SelectionList)
            first = lister.options[0].value
            await pilot.pause()
            lister.deselect(first)
            await pilot.pause()
            assert tick(lister, 0) == "[ ]"
            lister.select(first)
            await pilot.pause()
            assert tick(lister, 0) == "[x]"

    asyncio.run(run())


def test_palette_sheet_artifacts_follow_the_session_field(deps):
    """`sheet --artifacts` ticks one session's outputs and re-ticks when the session changes."""
    from io import BytesIO

    from textual.widgets import Input, Select, SelectionList

    deps_value, _ = deps
    deps_value.execute = lambda command, arguments: ("succeeded", {})
    first = f"ses_{0:032x}"
    second = "ses_" + "9" * 32
    deps_value.repositories.insert("sessions", {
        "id": second, "name": "second", "version": 0, "created_at": "2026-10-24T00:00:00Z",
    })
    seeded = {first: [], second: []}
    for session, seeds in ((first, "ab"), (second, "cd")):
        for seed in seeds:
            identity = "art_" + seed * 32
            buffer = BytesIO()
            Image.new("RGB", (8, 8), "red").save(buffer, format="PNG")
            deps_value.store.stage(identity, buffer.getvalue())
            deps_value.store.publish(identity)
            deps_value.repositories.insert("artifact_links", {
                "session_id": session, "artifact_id": identity, "role": "output",
                "created_at": "2026-10-24T00:00:00Z",
            })
            seeded[session].append(identity)

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._open_command("sheet")
            await pilot.pause()
            form = app.screen
            lister = form.query_one("#artifacts", SelectionList)
            assert [item.value for item in lister.options] == seeded[first]
            assert form.values()["artifacts"] == ",".join(seeded[first])
            # Naming another session replaces the list, so a sheet cannot be built from a mix.
            form.query_one("#session-choice", Select).value = second
            await pilot.pause()
            assert [item.value for item in lister.options] == seeded[second]
            assert form.values()["artifacts"] == ",".join(seeded[second])
            # Typing the session by hand does the same, and stays possible.
            assert isinstance(form.query_one("#session", Input), Input)
            form.query_one("#session", Input).value = first
            await pilot.pause()
            assert [item.value for item in lister.options] == seeded[first]

    asyncio.run(run())


def test_checklist_untick_needs_no_button_widget(deps):
    """The tick box is drawn inline by Textual, so a click on the row must still toggle it."""
    from textual.widgets import SelectionList

    deps_value, _ = deps
    deps_value.execute = lambda command, arguments: ("succeeded", {})

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._open_command("skills install")
            await pilot.pause()
            form = app.screen
            lister = form.query_one("#target", SelectionList)
            first = lister.options[0].value
            before = form.values()["target"].split(",")
            assert first in before
            await pilot.click("#target", offset=(3, 0))
            await pilot.pause()
            assert str(lister.options[0].prompt).startswith("[ ]")
            after = form.values()["target"].split(",")
            assert first not in after
            await pilot.click("#target", offset=(3, 0))
            await pilot.pause()
            assert str(lister.options[0].prompt).startswith("[x]")
            # Re-ticking appends, so compare as a set rather than as an order.
            assert set(form.values()["target"].split(",")) == set(before)

    asyncio.run(run())


def test_palette_transform_offers_the_cli_operator_and_preset_names(deps):
    """`--op`/`--preset` are typed from memory otherwise; both come from the CLI's own lists."""
    from textual.widgets import Input, Select

    deps_value, _ = deps
    deps_value.execute = lambda command, arguments: ("succeeded", {})

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._open_command("transform")
            await pilot.pause()
            form = app.screen
            ops = [value for _, value in form.query_one("#op-choice", Select)._options]
            presets = [value for _, value in form.query_one("#preset-choice", Select)._options]
            assert "superres" in ops
            assert "kuwahara" in ops
            assert "线稿" in presets
            # Params are appended by hand (`superres:model=...,accept=true`), so typing stays.
            assert isinstance(form.query_one("#op", Input), Input)
            assert isinstance(form.query_one("#preset", Input), Input)
            form.query_one("#op", Input).value = "superres:model=realesr-animevideov3,accept=true"
            assert form.values()["op"].startswith("superres:")

    asyncio.run(run())


def test_transform_panel_asks_before_running_a_local_model(deps):
    """Local weights are selectable, but the model and its licence are answered first."""
    from io import BytesIO

    from textual.widgets import DataTable

    from imagen_cli.application import superres

    deps_value, _ = deps
    calls = []

    def transform(artifact, pipeline, session):
        calls.append((artifact, pipeline, session))
        return {"artifact": {"id": "art_" + "e" * 32}}

    deps_value.transform = transform
    identity = "art_" + "e" * 32
    session = f"ses_{0:032x}"
    buffer = BytesIO()
    Image.new("RGB", (16, 16), "red").save(buffer, format="PNG")
    deps_value.store.stage(identity, buffer.getvalue())
    deps_value.store.publish(identity)
    deps_value.repositories.insert("artifact_links", {
        "session_id": session, "artifact_id": identity, "role": "output",
        "created_at": "2026-10-24T00:00:00Z",
    })

    async def run():
        app = WorkbenchApp(deps_value)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("t")
            await pilot.pause()
            screen = app.screen
            assert "superres" in screen.names
            assert screen.entries["superres"]["group"] == "模型恢复"
            screen.table.move_cursor(row=screen.names.index("superres"))
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert type(app.screen).__name__ == "LocalModelScreen"
            assert calls == []  # nothing runs before the question is answered
            table = app.screen.query_one("#local-model-table", DataTable)
            rows = [
                [str(cell) for cell in table.get_row_at(index)]
                for index in range(table.row_count)
            ]
            assert [row[0] for row in rows] == [m["model"] for m in superres.catalog()["models"]]
            assert ("已下载" in rows[0][-1]) is superres.catalog()["models"][0]["downloaded"]
            index = next(i for i, row in enumerate(rows) if row[0] == "realesr-animevideov3")
            table.move_cursor(row=index)
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert len(calls) == 1
            assert calls[0][1] == [
                {"op": "superres", "params": {"model": "realesr-animevideov3", "accept": True}}
            ]

    asyncio.run(run())
