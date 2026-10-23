"""Terminal UI: browsing, confirmed writes and a local transform panel."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from PIL import Image
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, Static

from imagen_cli.application import cache_service, media_ops
from imagen_cli.cli.human import render as human_render
from imagen_cli.cli.options import SharedOptions
from imagen_cli.cli.registry import COMMANDS, discover
from imagen_cli.cli.renderers import data_error, error_message
from imagen_cli.storage import queries
from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.private_state import PrivateStateStore
from imagen_cli.storage.repositories import Repositories

from . import preview

PRESETS: dict[str, list[dict[str, Any]]] = {
    "像素化": [{"op": "pixelate", "params": {"scale": 4}}],
    "调色板": [{"op": "quantize", "params": {"colors": 16, "dither": True}}],
    "线稿": [{"op": "grayscale"}, {"op": "autocontrast"},
             {"op": "highpass", "params": {"radius": 2, "boost": 2}}],
    "抖动": [{"op": "dither", "params": {"mode": "atkinson"}}],
    "锐化": [{"op": "sharpen", "params": {"radius": 2, "percent": 160}}],
    "赛璐璐": [{"op": "cel_shade", "params": {"levels": 3}}],
}


@dataclass
class Deps:
    repositories: Repositories
    store: ArtifactStore
    audit: Callable[..., list[cache_service.Entry]] = cache_service.audit
    purge: Callable[..., dict[str, Any]] = cache_service.purge
    delete_session: Callable[[Repositories, str], dict[str, Any]] | None = None
    fork: Callable[..., tuple[str, dict[str, Any]]] | None = None
    export: Callable[..., tuple[str, dict[str, Any]]] | None = None
    transform: Callable[..., dict[str, Any]] | None = None
    profile_set: Callable[[dict[str, Any]], tuple[str, dict[str, Any]]] | None = None
    models_refresh: Callable[[str], tuple[str, dict[str, Any]]] | None = None
    models_add: Callable[[str, str], tuple[str, dict[str, Any]]] | None = None
    models_use: Callable[[str, str], tuple[str, dict[str, Any]]] | None = None
    open_artifact: Callable[[str, bool], tuple[str, dict[str, Any]]] | None = None
    create_session: Callable[..., dict[str, Any]] | None = None
    execute: Callable[[str, dict[str, Any]], tuple[str, dict[str, Any]]] | None = None
    secret_ref: Callable[[str, str], str] | None = None
    private_store: PrivateStateStore | None = None
    workspace_root: Path | None = None
    options: SharedOptions | None = None

    def __post_init__(self) -> None:
        if self.delete_session is None:
            from imagen_cli.application.cleanup_service import delete_session

            self.delete_session = delete_session
        if self.fork is None:
            self.fork = self._fork
        if self.export is None:
            self.export = self._export
        if self.transform is None:
            self.transform = self._transform
        if self.profile_set is None:
            self.profile_set = self._profile_set
        if self.models_refresh is None:
            self.models_refresh = self._models_refresh
        if self.models_add is None:
            self.models_add = self._models_add
        if self.models_use is None:
            self.models_use = self._models_use
        if self.open_artifact is None:
            self.open_artifact = self._open_artifact
        if self.create_session is None:
            self.create_session = self._create_session
        if self.secret_ref is None:
            self.secret_ref = self._secret_ref

    def _fork(
        self, session_id: str, *, turn_id: str, name: str, artifact_ids: tuple[str, ...],
    ) -> tuple[str, dict[str, Any]]:
        from imagen_cli.cli.commands import history

        return history.fork_(
            self.repositories, session_id, turn_id=turn_id, name=name,
            artifact_ids=artifact_ids,
        )

    def _export(self, session_id: str, destination: str) -> tuple[str, dict[str, Any]]:
        from imagen_cli.cli.commands import history

        return history.export_(self.repositories, self.store, session_id, destination)

    def _transform(
        self, artifact: str, pipeline: list[dict[str, Any]], session: str,
    ) -> dict[str, Any]:
        from imagen_cli.application import postprocess_service

        return postprocess_service.apply(
            self.repositories, self.store, artifact, pipeline, session_id=session,
        )

    def _profile_set(self, parameters: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        from imagen_cli.cli.commands import profile as profile_command

        data = profile_command.build_set(parameters)
        return profile_command.execute(
            self.repositories, "profile set", profile=parameters["profile"], data=data,
        )

    def _models_refresh(self, profile: str) -> tuple[str, dict[str, Any]]:
        from functools import partial

        from imagen_cli.cli.commands import models
        from imagen_cli.execution.configured import resolve_credential

        return models.refresh_(
            self.repositories, profile, send_credential=True,
            resolve_credential=partial(
                resolve_credential, private_store=self.private_store
            ),
        )

    def _models_add(self, profile: str, model: str) -> tuple[str, dict[str, Any]]:
        from imagen_cli.cli.commands import models

        return models.add_(self.repositories, profile, model)

    def _models_use(self, profile: str, model: str) -> tuple[str, dict[str, Any]]:
        from imagen_cli.cli.commands import models

        return models.use_(self.repositories, profile, model)

    def _open_artifact(self, artifact: str, folder: bool) -> tuple[str, dict[str, Any]]:
        from imagen_cli.application.opener import open_path

        path = self.store.get(artifact)
        if path is None:
            return "not_found", {}
        open_path(path, folder=folder)
        return "succeeded", {
            "artifact": artifact, "path": str(path.parent if folder else path),
            "opened": "folder" if folder else "image",
        }

    def _secret_ref(self, profile: str, key: str) -> str:
        if self.private_store is None:
            raise ValueError("private credential storage is unavailable")
        return self.private_store.write_credential(profile, key)

    def _create_session(
        self, name: str, intent: str = "", profile: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        from imagen_cli.application.session_service import SessionService

        row = SessionService(self.repositories, self.store).create_session(
            name, intent=intent, default_profile_id=profile, default_model=model,
        )
        return row.model_dump(mode="json")


def _sanitize(value: object) -> str:
    return "".join(char for char in str(value) if char.isprintable())


def _executor(deps: Deps) -> Callable[[str, dict[str, Any]], tuple[str, dict[str, Any]]]:
    """Workers get their own connection; the app's connection stays on the UI thread."""
    assert deps.execute is not None
    if deps.workspace_root is None:
        return deps.execute
    root = str(deps.workspace_root)
    options = deps.options or SharedOptions()

    def run(command: str, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        from imagen_cli.application.container import Container

        with Container(workspace=root, options=options) as container:
            return container.execute(command, arguments)

    return run


_TONES = {
    "muted": "#98a0ad",
    "info": "#8fd3e8",
    "ok": "#8fc7a8",
    "error": "#e98f95",
}

# Schema field names stay the machine contract; only the visible label is Chinese.
_FIELD_LABELS = {
    "prompt": "提示词",
    "profile": "配置",
    "model": "模型",
    "count": "数量",
    "size": "尺寸",
    "name": "名称",
    "intent": "意图",
    "session": "会话",
    "operation": "操作",
    "artifact": "产物",
    "artifact_id": "产物",
    "plan_id": "计划",
    "destination": "目标路径",
    "provider": "供应商",
    "family": "端点类型",
    "base_url": "端点",
    "model_default": "默认模型",
    "credential_ref": "凭证引用",
    "api_key": "API key",
    "revision": "修订",
    "limit": "条数",
    "cursor": "游标",
    "digest": "摘要",
    "pipeline": "流水线",
    "op": "操作",
    "archive": "归档路径",
    "dry_run": "仅演示",
    "yes": "确认",
    "folder": "目录",
    "send_credential": "发送凭证",
    "timeout": "超时",
    "wait": "等待完成",
    "async_": "异步",
    "concurrency": "并发",
    "skill": "技能",
    "scope": "范围",
    "force": "覆盖",
}


def _label(name: str) -> str:
    return _FIELD_LABELS.get(name, name)


def _keybar(entries: list[tuple[str, str]]) -> Text:
    text = Text()
    for index, (key, label) in enumerate(entries):
        if index:
            text.append(" ")
        text.append(f" {key} ", style="bold #1a1014 on #f2a6c0")
        text.append(f" {label}", style="#9aa2af")
    return text


class ConfirmScreen(ModalScreen[bool]):
    """Typed confirmation; Enter with the exact word authorizes the action."""

    BINDINGS = [Binding("escape", "cancel", "取消")]

    def __init__(self, prompt: str, word: str) -> None:
        super().__init__()
        self.prompt = prompt
        self.word = word
        self.field = Input(placeholder=word)

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(self.prompt, classes="panel-title"),
            self.field,
            Static("enter 确认 · esc 取消", classes="panel-hint"),
            classes="panel", id="confirm",
        )

    def on_mount(self) -> None:
        self.field.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value == self.word)

    def action_cancel(self) -> None:
        self.dismiss(False)


class FormScreen(ModalScreen[dict[str, str] | None]):
    """Small labelled form; Enter submits, Escape cancels."""

    BINDINGS = [Binding("escape", "cancel", "取消")]

    def __init__(self, title: str, fields: list[tuple[str, str, str]],
                 optional: set[str] | None = None,
                 secrets: set[str] | None = None) -> None:
        super().__init__()
        self.heading = title
        self.fields = fields
        self.optional = optional or set()
        self.secrets = secrets or set()
        self.inputs = [
            Input(value=default, placeholder=placeholder, id=name,
                  password=name in self.secrets)
            for name, placeholder, default in fields
        ]

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(self.heading, classes="panel-title"),
            *(Horizontal(Static(_label(name), classes="field-label"), field, classes="field")
              for (name, _, _), field in zip(self.fields, self.inputs, strict=True)),
            Static("enter 提交 · esc 取消", classes="panel-hint"),
            classes="panel", id="form",
        )

    def on_mount(self) -> None:
        if self.inputs:
            self.inputs[0].focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        values = {str(field.id): field.value.strip() for field in self.inputs}
        missing = [
            name for name, value in values.items()
            if not value and name not in self.optional
        ]
        if missing:
            self.query_one(Static).update(
                self.heading + " · 必填：" + "、".join(_label(name) for name in missing)
            )
            return
        self.dismiss({name: value for name, value in values.items() if value})

    def action_cancel(self) -> None:
        self.dismiss(None)


class CacheScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("p", "plan", "生成摘要"),
        Binding("escape", "dismiss_screen", "关闭"),
    ]

    def __init__(self, deps: Deps) -> None:
        super().__init__()
        self.deps = deps
        self.entries: list[cache_service.Entry] = []
        self.digest = ""
        self.table: DataTable[Any] = DataTable()
        self.status = Static("")
        self.field = Input(placeholder="输入 purge 确认")

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("可回收空间", classes="panel-title"),
            self.table, self.status, self.field,
            classes="panel", id="cache",
        )

    def on_mount(self) -> None:
        self.table.cursor_type = "row"
        self.table.add_columns("类型", "条目", "字节")
        extra = {} if self.deps.private_store is None else {
            "private_store": self.deps.private_store
        }
        self.entries = self.deps.audit(self.deps.repositories, self.deps.store, **extra)
        for entry in self.entries:
            self.table.add_row(_sanitize(entry.kind), _sanitize(entry.identity), str(entry.bytes))
        self.status.update(f"{len(self.entries)} 项，共 {sum(e.bytes for e in self.entries)} 字节")

    def action_plan(self) -> None:
        self.digest = cache_service.digest(self.entries)
        self.status.update(f"摘要 {self.digest[:16]}…，输入 purge 确认")
        self.field.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.value != "purge" or not self.digest:
            self.status.update("未确认")
            return
        extra = {} if self.deps.private_store is None else {
            "private_store": self.deps.private_store
        }
        result = self.deps.purge(
            self.deps.repositories, self.deps.store, self.digest, **extra
        )
        self.status.update(f"已删除 {result['deleted_bytes']} 字节")
        self.dismiss(True)

    def action_dismiss_screen(self) -> None:
        self.dismiss(False)


class TransformScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("up", "cursor_up", "上一个", show=False),
        Binding("down", "cursor_down", "下一个", show=False),
        Binding("enter", "apply", "应用"),
        Binding("escape", "cancel", "取消"),
    ]

    def __init__(self, deps: Deps, artifact: str, session: str, path: Path) -> None:
        super().__init__()
        self.deps = deps
        self.artifact = artifact
        self.session = session
        self.path = path
        self.names = list(PRESETS)
        self.table: DataTable[Any] = DataTable()
        self.status = Static("")
        self.view = Static("")

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel", id="transform"):
            yield Static("变换预设", classes="panel-title")
            with Horizontal():
                yield self.table
                with Vertical():
                    yield self.view
                    yield self.status
            yield Static("↑↓ 预览 · enter 应用 · esc 取消", classes="panel-hint")

    def on_mount(self) -> None:
        self.table.cursor_type = "row"
        self.table.add_columns("预设")
        for name in self.names:
            self.table.add_row(name)
        self.table.can_focus = False
        self.table.move_cursor(row=0)
        self.set_focus(None)
        self.render_preview()

    def action_cursor_up(self) -> None:
        self._move(-1)

    def action_cursor_down(self) -> None:
        self._move(1)

    def _move(self, delta: int) -> None:
        row = min(len(self.names) - 1, max(0, self.table.cursor_row + delta))
        self.table.move_cursor(row=row)
        self.render_preview()

    def render_preview(self) -> None:
        row = self.table.cursor_row
        if not 0 <= row < len(self.names):
            return
        name = self.names[row]
        try:
            with Image.open(self.path) as opened:
                opened.load()
                thumbnail = opened.convert("RGBA" if "A" in opened.getbands() else "RGB")
                thumbnail.thumbnail((48, 24))
                for step in PRESETS[name]:
                    thumbnail = media_ops.OPS[step["op"]](thumbnail, **step.get("params", {}))
            image = thumbnail.convert("RGB")
            self.view.update(Text.from_ansi(preview.ansi_image(image, cols=48, rows=12)))
            self.status.update(f"{name} · enter 应用")
        except (ValueError, KeyError, OSError) as error:
            self.view.update("预览不可用")
            self.status.update(_sanitize(error))

    def action_apply(self) -> None:
        row = self.table.cursor_row
        if not 0 <= row < len(self.names):
            return
        name = self.names[row]
        assert self.deps.transform is not None
        try:
            result = self.deps.transform(self.artifact, PRESETS[name], self.session)
        except (ValueError, KeyError, OSError) as error:
            self.status.update(_sanitize(error))
            return
        self.dismiss(bool(result))

    def action_cancel(self) -> None:
        self.dismiss(False)


class ModelPickerScreen(ModalScreen[str | None]):
    """Pick the default model from the declared catalog, with a text filter."""

    BINDINGS = [Binding("escape", "cancel", "关闭")]

    def __init__(self, models: list[dict[str, Any]], current: str) -> None:
        super().__init__()
        self.models = sorted(models, key=lambda row: str(row["model_id"]))
        self.current = current
        self.filter: Input = Input(placeholder="过滤 model id", id="model-filter")
        self.table: DataTable[Any] = DataTable(id="model-table")

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("选择默认模型", classes="panel-title"),
            self.filter, self.table,
            Static("输入过滤 · enter 选定 · esc 取消", classes="panel-hint"),
            classes="panel", id="model-picker",
        )

    def on_mount(self) -> None:
        self.table.cursor_type = "row"
        self.table.add_column("model")
        self._populate()
        self.filter.focus()

    def _populate(self) -> None:
        needle = self.filter.value.strip().lower()
        self.table.clear()
        for row in self.models:
            model = str(row["model_id"])
            if needle and needle not in model.lower():
                continue
            self.table.add_row(("● " if model == self.current else "  ") + model, key=model)

    def on_input_changed(self, _: Input.Changed) -> None:
        self._populate()

    def on_input_submitted(self, _: Input.Submitted) -> None:
        if self.table.row_count:
            key = self.table.coordinate_to_cell_key(self.table.cursor_coordinate).row_key
            self.dismiss(str(key.value) if key.value else None)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.dismiss(str(event.row_key.value) if event.row_key.value else None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ProvidersScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("a", "add", "新增"),
        Binding("u", "update", "更新"),
        Binding("m", "refresh_models", "刷新模型"),
        Binding("n", "add_model", "添加模型"),
        Binding("escape", "dismiss_screen", "关闭"),
    ]
    FIELDS = (
        ("profile", "profile id", ""),
        ("provider", "openai / gemini", "openai"),
        ("family", "images / generate_content", "images"),
        ("base_url", "https://…", ""),
        ("model_default", "model id", ""),
        ("credential_ref", "env:NAME / keyring:SERVICE / private:NAME（可留空）",
         "env:IMAGEN_KEY"),
        ("api_key", "直接填 API key（写入私有存储，不回显）", ""),
        ("revision", "1", "1"),
    )
    OPTIONAL = {"credential_ref", "api_key"}
    SECRETS = {"api_key"}

    def __init__(self, deps: Deps) -> None:
        super().__init__()
        self.deps = deps
        self.table: DataTable[Any] = DataTable()
        self.status = Static("")

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("Providers", classes="panel-title"),
            Static(
                "a 新增 · u 更新 · m 刷新模型 · n 手输模型 · enter 选择默认模型\n"
                "新增/更新时可直接填 API key，写入 workspace 私有存储；credential_ref 可留空",
                classes="panel-hint",
            ),
            self.table, self.status,
            classes="panel", id="providers",
        )

    def on_mount(self) -> None:
        self.table.cursor_type = "row"
        self.table.add_columns("id", "provider", "family", "model", "revision")
        self.refresh_rows()

    def refresh_rows(self) -> None:
        self.table.clear()
        for row in queries.iter_all(self.deps.repositories, "profiles"):
            self.table.add_row(
                _sanitize(row["id"]), _sanitize(row["provider"]), _sanitize(row["family"]),
                _sanitize(row["model_default"]), str(row["revision"]), key=row["id"],
            )

    def _selected(self) -> dict[str, Any] | None:
        if self.table.row_count == 0:
            return None
        key = self.table.coordinate_to_cell_key(self.table.cursor_coordinate).row_key
        if key.value is None:
            return None
        return cast(
            dict[str, Any] | None,
            self.deps.repositories.get("profiles", str(key.value), include_private=True),
        )

    def _with_secret(self, values: dict[str, str]) -> dict[str, Any]:
        parameters: dict[str, Any] = {
            key: value for key, value in values.items() if key != "api_key"
        }
        secret = values.get("api_key", "")
        if secret:
            if self.deps.secret_ref is None:
                raise ValueError("private credential storage is unavailable")
            parameters["credential_ref"] = self.deps.secret_ref(
                str(values["profile"]), secret
            )
        elif not parameters.get("credential_ref"):
            raise ValueError("填写 API key，或提供 env:/keyring:/private: 引用")
        return parameters

    def action_add(self) -> None:
        def done(values: dict[str, str] | None) -> None:
            if not values or self.deps.profile_set is None:
                return
            try:
                parameters = self._with_secret(values)
                status, data = self.deps.profile_set(
                    {**parameters, "revision": int(values.get("revision", "1"))}
                )
            except (ValueError, OSError) as error:
                self.status.update(_sanitize(error))
                return
            self.status.update(f"{status}: {_sanitize(data.get('profile', {}).get('id', ''))}")
            self.refresh_rows()

        self.app.push_screen(
            FormScreen(
                "新增 profile", list(self.FIELDS),
                optional=self.OPTIONAL, secrets=self.SECRETS,
            ),
            done,
        )

    def action_update(self) -> None:
        profile = self._selected()
        if profile is None:
            self.status.update("没有选中 profile")
            return
        defaults = [
            (name, placeholder,
             _sanitize(profile["id"] if name == "profile" else profile.get(name, value)))
            for name, placeholder, value in self.FIELDS
        ]

        def done(values: dict[str, str] | None) -> None:
            if not values or self.deps.profile_set is None:
                return
            try:
                parameters = self._with_secret(values)
                status, data = self.deps.profile_set(
                    {**parameters, "revision": int(profile["revision"])}
                )
            except (ValueError, OSError) as error:
                self.status.update(_sanitize(error))
                return
            self.status.update(f"{status}: revision {data.get('profile', {}).get('revision', '')}")
            self.refresh_rows()

        self.app.push_screen(
            FormScreen(
                "更新 profile", defaults,
                optional=self.OPTIONAL, secrets=self.SECRETS,
            ),
            done,
        )

    def action_refresh_models(self) -> None:
        profile = self._selected()
        if profile is None or self.deps.models_refresh is None:
            self.status.update("没有选中 profile")
            return
        try:
            status, data = self.deps.models_refresh(str(profile["id"]))
        except (ValueError, OSError) as error:
            self.status.update(_sanitize(error))
            return
        self.status.update(f"{status}: {len(data.get('models', []))} 个模型")

    def action_add_model(self) -> None:
        profile = self._selected()
        if profile is None or self.deps.models_add is None:
            self.status.update("没有选中 profile")
            return

        def done(values: dict[str, str] | None) -> None:
            if not values or self.deps.models_add is None:
                return
            try:
                status, _ = self.deps.models_add(str(profile["id"]), values["model"])
            except (ValueError, OSError) as error:
                self.status.update(_sanitize(error))
                return
            self.status.update(f"{status}: {values['model']}")

        self.app.push_screen(
            FormScreen("添加模型", [("model", "model id", "")]), done
        )

    def on_data_table_row_selected(self, _: DataTable.RowSelected) -> None:
        self.action_choose_model()

    def action_choose_model(self) -> None:
        profile = self._selected()
        if profile is None:
            self.status.update("没有选中 profile")
            return
        rows = queries.iter_all(
            self.deps.repositories, "provider_models", profile_id=str(profile["id"])
        )
        if not rows:
            self.status.update("尚未声明模型；按 m 刷新，或按 n 手输 model id")
            return

        def done(model: str | None) -> None:
            if not model or self.deps.models_use is None:
                return
            try:
                status, _ = self.deps.models_use(str(profile["id"]), model)
            except (ValueError, OSError) as error:
                self.status.update(_sanitize(error))
                return
            self.status.update(f"{status}: default {_sanitize(model)}")
            self.refresh_rows()

        self.app.push_screen(
            ModelPickerScreen(rows, str(profile.get("model_default") or "")), done
        )

    def action_dismiss_screen(self) -> None:
        self.dismiss(False)


class PlanScreen(ModalScreen[bool]):
    """Show a frozen plan; approval and execution are separate keys."""

    BINDINGS = [
        Binding("a", "approve", "批准"),
        Binding("enter", "execute", "执行"),
        Binding("r", "retry", "重试"),
        Binding("escape", "cancel", "取消"),
    ]

    def __init__(self, deps: Deps, plan: dict[str, Any]) -> None:
        super().__init__()
        self.deps = deps
        self.plan = plan
        self.approved = False
        self.running = False
        self.status = Static("")

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("执行计划", classes="panel-title"),
            Static(
                f"计划 {self.plan.get('id')}\n"
                f"操作 {self.plan.get('operation')} · 模型 {self.plan.get('model')}"
            ),
            Static(f"摘要 {self.plan.get('plan_digest')}", classes="panel-hint"),
            self.status,
            Static("a 批准 · enter 执行 · r 重试 · esc 取消", classes="panel-hint"),
            classes="panel", id="plan",
        )

    def on_mount(self) -> None:
        self._say("尚未批准", "muted")

    def _say(self, message: str, tone: str = "muted") -> None:
        self.status.update(Text(message, style=_TONES[tone]))

    def _approval_state(self) -> str | None:
        rows = self.deps.repositories.list("approvals", plan_id=self.plan["id"])
        return str(rows[0]["state"]) if rows else None

    def action_approve(self) -> None:
        if self.running:
            return
        if self.deps.execute is None:
            self._say("执行器不可用", "error")
            return
        state = self._approval_state()
        if state == "consumed":
            # The plan already dispatched once; Enter shows the recorded outcome.
            self.approved = True
            self._say("该计划已执行 · enter 查看结果", "info")
            return
        if state == "expired":
            self._say("该计划已过期 · esc 关闭后重新生成", "error")
            return
        try:
            status, _ = self.deps.execute(
                "plan approve",
                {"plan_id": self.plan["id"], "digest": self.plan["plan_digest"]},
            )
        except (ValueError, OSError) as error:
            self._say("批准失败：" + _sanitize(error), "error")
            return
        if status in {"approved", "succeeded"}:
            self.approved = True
            self._say("已批准 · enter 执行", "ok")
        else:
            self._say(f"批准失败：{status}", "error")

    def action_execute(self) -> None:
        if self.running:
            return
        if not self.approved:
            self._say("先按 a 批准", "info")
            return
        if self.deps.execute is None:
            self._say("执行器不可用", "error")
            return
        self.running = True
        self._say("执行中… 首次请求可能 20–60 秒，完成后自动返回", "info")
        # Owned by the app: dismissing this screen must not cancel the paid request.
        self.app.run_worker(self._execute_job, thread=True, group="plan-execute",
                            exclusive=True)

    def _execute_job(self) -> None:
        try:
            status, data = _executor(self.deps)(
                "plan execute", {"plan_id": self.plan["id"]}
            )
        except Exception as error:  # noqa: BLE001 - surfaced in the panel
            self.app.call_from_thread(self._finish, None, _sanitize(error))
            return
        self.app.call_from_thread(self._finish, (status, data), None)

    def _finish(self, result: tuple[str, dict[str, Any]] | None, error: str | None) -> None:
        self.running = False
        if error is not None:
            self._say("执行失败：" + error, "error")
            return
        assert result is not None
        status, data = result
        job = data.get("job") or {}
        if status in {"succeeded", "accepted", "partial_success"}:
            self._say(f"执行完成：{job.get('state')} · {job.get('id')}", "ok")
            self.dismiss(True)
        else:
            reason = error_message(job.get("error") or data.get("error"))
            identity = job.get("id")
            self._say(
                f"执行结果：{status}"
                + (f" · {reason}" if reason else "")
                + (f" · {identity}" if identity else ""),
                "error",
            )

    def action_retry(self) -> None:
        if self.running:
            return
        if self.deps.execute is None:
            self._say("执行器不可用", "error")
            return
        try:
            status, data = self.deps.execute("plan retry", {"plan_id": self.plan["id"]})
        except (ValueError, OSError) as error:
            self._say("重试失败：" + _sanitize(error), "error")
            return
        plan = data.get("plan") or {}
        if plan.get("id"):
            self.app.push_screen(PlanScreen(self.deps, plan))
        else:
            self._say(f"重试结果：{status}", "error")

    def action_cancel(self) -> None:
        if self.running:
            self._say("执行中，请稍候…", "info")
            return
        self.dismiss(False)


def _convert_arguments(command: str, values: dict[str, str]) -> dict[str, Any]:
    schema = discover(command)["input_schema"]
    properties = schema.get("properties") or {}
    arguments: dict[str, Any] = {}
    for name, raw in values.items():
        spec = properties.get(name) or {}
        kind = spec.get("type")
        if kind is None and "anyOf" in spec:
            kind = next(
                (item.get("type") for item in spec["anyOf"] if item.get("type")), None
            )
        if not raw:
            continue
        if kind == "integer":
            arguments[name] = int(raw)
        elif kind == "boolean":
            arguments[name] = raw.lower() in {"1", "true", "yes", "on"}
        elif kind == "array":
            if raw.lstrip().startswith("["):
                import json

                arguments[name] = json.loads(raw)
            else:
                arguments[name] = [
                    item.strip() for item in raw.split(",") if item.strip()
                ]
        elif kind == "object":
            import json

            arguments[name] = json.loads(raw)
        else:
            arguments[name] = raw
    return arguments


class ResultScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "关闭")]

    def __init__(self, command: str, status: str, data: dict[str, Any]) -> None:
        super().__init__()
        self.command = command
        self.status = status
        self.data = data
        self.body = Static("")

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(f"{self.command} · {self.status}", classes="panel-title"),
            self.body,
            Static("esc 关闭", classes="panel-hint"),
            classes="panel", id="result",
        )

    def on_mount(self) -> None:
        ok = self.status in {"succeeded", "accepted", "approved", "planned"}
        message = error_message(data_error(self.data)) or str(
            self.data.get("message") or self.status
        )
        value = {
            "ok": ok, "status": self.status, "data": self.data,
            "error": None if ok else {"message": message},
        }
        self.body.update(human_render(value, color=False))

    def action_close(self) -> None:
        self.dismiss(None)


class CommandPaletteScreen(ModalScreen[str | None]):
    """Every registered CLI command; the form is generated from its schema."""

    BINDINGS = [
        Binding("up", "cursor_up", "上一个", show=False),
        Binding("down", "cursor_down", "下一个", show=False),
        Binding("enter", "select", "打开"),
        Binding("escape", "close", "关闭"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.names = sorted(
            name for name, command in COMMANDS.items()
            if command.available and name not in {"tui", "help", "schema"}
        )
        self.matches = list(self.names)
        self.table: DataTable[Any] = DataTable()
        self.filter: Input = Input(placeholder="过滤命令或说明", id="palette-filter")
        self.status = Static("")

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("全部命令", classes="panel-title"),
            self.filter, self.table, self.status,
            Static("输入过滤 · ↑↓ 选择 · enter 打开 · esc 关闭", classes="panel-hint"),
            classes="panel", id="palette",
        )

    def on_mount(self) -> None:
        self.table.cursor_type = "row"
        self.table.add_columns("命令", "说明")
        self.table.can_focus = False
        self._populate()
        self.filter.focus()

    def _populate(self) -> None:
        needle = self.filter.value.strip().lower()
        self.matches = [
            name for name in self.names
            if not needle
            or needle in name.lower()
            or needle in str(COMMANDS[name].summary).lower()
        ]
        self.table.clear()
        for name in self.matches:
            self.table.add_row(_sanitize(name), _sanitize(COMMANDS[name].summary))
        if self.matches:
            self.table.move_cursor(row=0)
        self.status.update(f"{len(self.matches)} 个命令")

    def on_input_changed(self, _: Input.Changed) -> None:
        self._populate()

    def on_input_submitted(self, _: Input.Submitted) -> None:
        self.action_select()

    def _move(self, delta: int) -> None:
        if not self.matches:
            return
        row = min(len(self.matches) - 1, max(0, self.table.cursor_row + delta))
        self.table.move_cursor(row=row)

    def action_cursor_up(self) -> None:
        self._move(-1)

    def action_cursor_down(self) -> None:
        self._move(1)

    def action_select(self) -> None:
        row = self.table.cursor_row
        if 0 <= row < len(self.matches):
            self.dismiss(self.matches[row])

    def action_close(self) -> None:
        self.dismiss(None)


def command_fields(command: str) -> tuple[list[tuple[str, str, str]], set[str]]:
    schema = discover(command)["input_schema"]
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    fields = []
    for name in sorted(properties):
        spec = properties[name] if isinstance(properties[name], dict) else {}
        kind = spec.get("type")
        if kind is None and "anyOf" in spec:
            kind = next(
                (item.get("type") for item in spec["anyOf"] if item.get("type")), None
            )
        default = spec.get("default", "")
        if default in {"", None} and spec.get("const") is not None:
            default = spec["const"]
        if isinstance(default, bool):
            default = "true" if default else "false"
        placeholder = str(kind or "value")
        if "enum" in spec:
            placeholder = " | ".join(str(item) for item in spec["enum"])
        fields.append((name, placeholder, str(default) if default != "" else ""))
    return fields, set(properties) - required


class HelpScreen(ModalScreen[None]):
    """Every shown binding in one panel; keeps the key bar short."""

    BINDINGS = [Binding("escape", "close", "关闭")]

    def __init__(self, rows: list[tuple[str, str]]) -> None:
        super().__init__()
        self.rows = rows

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("快捷键", classes="panel-title"),
            *(Static(f"{key}  {description}") for key, description in self.rows),
            Static(": 打开全部命令 · esc 关闭", classes="panel-hint"),
            classes="panel", id="help",
        )

    def action_close(self) -> None:
        self.dismiss(None)


class WorkbenchApp(App[None]):
    CSS = """
    Screen {
        background: #0c0e13; color: #ece7e4;
        scrollbar-color: #333a47; scrollbar-background: #10131a;
        scrollbar-color-hover: #f2a6c0; scrollbar-color-active: #f2a6c0;
        scrollbar-background-hover: #10131a; scrollbar-background-active: #10131a;
    }

    #topbar { height: 1; background: #11141b; padding: 0 1; }
    #brand { width: auto; color: #f2a6c0; text-style: bold; padding: 0 1 0 0; }
    #workspace { width: 1fr; color: #8e97a4; content-align: right middle; }

    #body { height: 1fr; }

    #sidebar {
        width: 36; background: #10131a; border-right: solid #252b36;
        padding: 1 1 0 1;
    }
    #sessions { height: 1fr; background: #10131a; }

    DataTable {
        background: transparent; color: #ddd8d4;
        scrollbar-color: #333a47; scrollbar-background: #141821;
        scrollbar-color-hover: #f2a6c0; scrollbar-color-active: #f2a6c0;
        scrollbar-size-vertical: 1; scrollbar-size-horizontal: 0;
    }
    DataTable > .datatable--header { background: transparent; color: #8e97a4; text-style: bold; }
    DataTable > .datatable--cursor { background: #f2a6c0; color: #1a1014; text-style: bold; }
    DataTable > .datatable--hover { background: #1b202a; }

    #detail-pane { width: 1fr; padding: 1 2 0 2; }
    #detail { color: #f4efec; text-style: bold; height: 1; overflow: hidden; }
    #meta { color: #8e97a4; height: 1; overflow: hidden; }
    #preview-frame {
        height: 1fr; margin-top: 1; background: #090b10;
        border: solid #252b36; align: center middle; padding: 1;
    }
    #preview { width: auto; height: auto; color: #a3abb7; text-align: center; }
    #film { height: 1; overflow: hidden; }
    #status { height: 1; overflow: hidden; }

    #keybar { height: 1; background: #11141b; padding: 0 1; }

    ModalScreen { align: center middle; background: #0c0e13; }
    .panel {
        background: #141821; border: solid #2c3442; padding: 1 2;
        width: 80%; max-width: 96; height: auto; max-height: 94%; overflow-y: auto;
        scrollbar-color: #333a47; scrollbar-background: #141821;
        scrollbar-color-hover: #f2a6c0; scrollbar-color-active: #f2a6c0;
    }
    #providers, #palette, #model-picker { height: 90%; overflow: hidden; }
    #providers DataTable, #palette DataTable, #model-picker DataTable {
        height: 1fr; width: 1fr;
    }
    #palette-filter { margin-bottom: 1; width: 1fr; }
    .panel-title { color: #f4efec; text-style: bold; padding-bottom: 1; }
    .panel-hint { color: #8e97a4; }
    .field { height: auto; }
    .field-label { width: 14; color: #98a0ad; content-align: left middle; padding-top: 1; }
    .field Input { width: 1fr; }
    Input { background: #0e1117; border: solid #252b36; color: #ece7e4; padding: 0 1; }
    Input:focus { border: solid #f2a6c0; }
    """
    BINDINGS = [
        Binding("q", "quit", "退出"),
        Binding("c", "cache", "缓存"),
        Binding("v", "providers", "Providers"),
        Binding("p", "providers", "Providers", show=False),
        Binding("n", "new_session", "新建会话"),
        Binding("g", "generate", "生成"),
        Binding("m", "edit", "编辑"),
        Binding("x", "delete", "删除会话"),
        Binding("f", "fork", "分支会话"),
        Binding("e", "export", "导出会话"),
        Binding("t", "transform", "变换"),
        Binding("o", "open_image", "打开图片"),
        Binding("O", "open_folder", "打开目录"),
        Binding("[", "previous_artifact", "上一张产物"),
        Binding("]", "next_artifact", "下一张产物"),
        Binding("r", "refresh", "刷新"),
        Binding("colon", "commands", "全部命令"),
        Binding("?", "help", "帮助"),
    ]

    def __init__(self, deps: Deps, *, workspace_label: str = "") -> None:
        super().__init__()
        self.deps = deps
        self.workspace_label = workspace_label
        self.sessions: DataTable[Any] = DataTable(id="sessions")
        self.detail = Static(id="detail")
        self.meta = Static(id="meta")
        self.preview = Static(id="preview")
        self.film = Static(id="film")
        self.status = Static(id="status")
        self.current: str | None = None
        self.film_items: list[dict[str, Any]] = []
        self.film_index = -1
        self.planning = False
        self.meta_base = ""

    def compose(self) -> ComposeResult:
        yield Horizontal(
            Static("IMAGEN", id="brand"),
            Static(self.workspace_label, id="workspace"),
            id="topbar",
        )
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield self.sessions
            with Vertical(id="detail-pane"):
                yield self.detail
                yield self.meta
                with Vertical(id="preview-frame"):
                    yield self.preview
                yield self.film
                yield self.status
        yield Static(_keybar([
            ("g", "生成"), ("m", "编辑"), ("t", "变换"), ("n", "新建"),
            ("v", "配置"), ("x", "删除"), ("[ ]", "产物"), (":", "命令"), ("?", "帮助"),
        ]), id="keybar")

    def on_mount(self) -> None:
        self.title = "Imagen"
        self.sessions.cursor_type = "row"
        self.sessions.add_columns("会话", "轮次", "版本")
        self.refresh_rows()

    def _say(self, message: str, tone: str = "muted") -> None:
        self.status.update(Text(message, style=_TONES[tone]))

    def refresh_rows(self) -> None:
        self.sessions.clear()
        sessions = [
            row for row in queries.iter_all(self.deps.repositories, "sessions")
            if row.get("deleted_at") is None
        ]
        for row in sessions:
            turns = len(queries.iter_all(self.deps.repositories, "turns", session_id=row["id"]))
            self.sessions.add_row(
                _sanitize(row["name"]), str(turns), str(row["version"]), key=row["id"],
            )
        identities = [str(row["id"]) for row in sessions]
        if self.current not in identities:
            self.current = identities[0] if identities else None
        if not identities:
            self.detail.update("尚无会话")
            self.meta.update("")
            self.film_items = []
            self.film_index = -1
            self.film.update("")
            self.preview.update(Text("尚无产物\n\n按 n 新建会话", justify="center"))
            self._say("按 n 新建会话，v 选择端点与模型", "info")
            return
        self.sessions.move_cursor(row=identities.index(str(self.current)))
        self.show_detail()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        # Repopulating the table queues highlights for old positions; only the live cursor counts.
        if self.sessions.row_count == 0 or event.row_key != self.sessions.coordinate_to_cell_key(
            self.sessions.cursor_coordinate
        ).row_key:
            return
        identity = str(event.row_key.value)
        if identity != self.current:
            self.current = identity
            self._say("")
        self.show_detail()

    def on_resize(self) -> None:
        self.show_detail()

    def _artifact_item(
        self, identity: str, turn: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        artifact = self.deps.repositories.get("artifacts", identity)
        path = self.deps.store.get(identity)
        if artifact is None or path is None:
            return None
        source = turn or {}
        return {
            "id": identity,
            "path": path,
            "state": str(source.get("creation_state") or ""),
            "operation": str(source.get("operation") or ""),
            "width": artifact.get("width"),
            "height": artifact.get("height"),
        }

    def _load_artifacts(self) -> list[dict[str, Any]]:
        """One entry per published output, oldest turn first."""
        if self.current is None:
            return []
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        turns = sorted(
            queries.iter_all(self.deps.repositories, "turns", session_id=self.current),
            key=lambda row: (row.get("created_at", ""), row["id"]),
        )
        for turn in turns:
            references = sorted(
                queries.iter_all(
                    self.deps.repositories, "artifact_references", turn_id=turn["id"]
                ),
                key=lambda row: int(row.get("index") or 0),
            )
            for reference in references:
                if reference.get("role") != "output":
                    continue
                identity = str(reference["artifact_id"])
                item = self._artifact_item(identity, turn)
                if item is not None and identity not in seen:
                    seen.add(identity)
                    items.append(item)
        for link in queries.iter_all(
            self.deps.repositories, "artifact_links", session_id=self.current
        ):
            identity = str(link["artifact_id"])
            if identity in seen:
                continue
            item = self._artifact_item(identity, None)
            if item is not None:
                seen.add(identity)
                items.append(item)
        return items

    def _current_artifact(self) -> tuple[str, Path] | None:
        items = self.film_items or self._load_artifacts()
        if not items:
            return None
        index = self.film_index if 0 <= self.film_index < len(items) else len(items) - 1
        return str(items[index]["id"]), cast(Path, items[index]["path"])

    def _select_artifact(self, index: int) -> None:
        if not self.film_items:
            return
        self.film_index = max(0, min(len(self.film_items) - 1, index))
        self._render_film()
        self._render_preview()

    def action_previous_artifact(self) -> None:
        self._select_artifact((self.film_index if self.film_index >= 0
                               else len(self.film_items)) - 1)

    def action_next_artifact(self) -> None:
        self._select_artifact((self.film_index if self.film_index >= 0
                               else len(self.film_items) - 1) + 1)

    def _render_film(self) -> None:
        if not self.film_items:
            self.film.update("")
            return
        # Keep a window around the selection so a long session stays one line.
        width = self.film.size.width or 0
        limit = max(3, min(9, (width - 14) // 5)) if width else 9
        start = max(0, min(len(self.film_items) - limit, self.film_index - limit // 2))
        window = self.film_items[start:start + limit]
        text = Text()
        text.append("产物 ", style="#8e97a4")
        text.append(f"{self.film_index + 1}/{len(self.film_items)}  ", style="#8e97a4")
        if start:
            text.append("… ", style="#5f6875")
        for offset, item in enumerate(window):
            index = start + offset
            if index == self.film_index:
                style = "bold #1a1014 on #f2a6c0"
            elif item["state"] == "failed":
                style = "#e98f95"
            elif item["state"] in {"queued", "dispatching", "running"}:
                style = "#8fd3e8"
            else:
                style = "#98a0ad"
            if offset:
                text.append(" ")
            text.append(f" {index + 1:02d} ", style=style)
        if start + limit < len(self.film_items):
            text.append(" …", style="#5f6875")
        self.film.update(text)

    def _render_preview(self) -> None:
        if not self.film_items or not 0 <= self.film_index < len(self.film_items):
            self.preview.update(Text("尚无产物\n\n按 g 生成第一张图", justify="center"))
            return
        item = self.film_items[self.film_index]
        path = cast(Path, item["path"])
        artifact = self.deps.repositories.get("artifacts", str(item["id"]))
        if artifact is None:
            self.preview.update(Text("产物不可用", justify="center"))
            return
        width, height = artifact.get("width"), artifact.get("height")
        size = f"{width}×{height}" if width and height else ""
        self.meta.update(" · ".join(part for part in (self.meta_base, size) if part))
        if preview.supported():
            frame = self.query_one("#preview-frame")
            cols = max(20, frame.size.width - 2)
            rows = max(5, frame.size.height - 2)
            self.preview.update(Text.from_ansi(preview.ansi_blocks(path, cols=cols, rows=rows)))
        else:
            self.preview.update(preview.describe(artifact, path))

    def show_detail(self) -> None:
        if self.current is None:
            return
        row = self.deps.repositories.get("sessions", self.current)
        if row is None:
            return
        self.detail.update(f"{_sanitize(row['name'])} · 版本 {row['version']}")
        profile, model = self._creation_defaults(row)
        model = model.rsplit("/", 1)[-1]
        self.meta_base = " · ".join(
            part for part in (profile, model) if part
        ) or "尚未选择端点与模型"
        self.meta.update(self.meta_base)
        previous = (
            str(self.film_items[self.film_index]["id"])
            if 0 <= self.film_index < len(self.film_items) else None
        )
        items = self._load_artifacts()
        identities = [str(item["id"]) for item in items]
        self.film_items = items
        if not items:
            self.film_index = -1
        elif previous in identities:
            self.film_index = identities.index(previous)
        else:
            self.film_index = len(items) - 1
        self._render_film()
        self._render_preview()

    def _push_plan(self, status: str, data: dict[str, Any], label: str = "生成") -> None:
        plan = data.get("plan") or {}
        if status not in {"approval_required", "planned"} or not plan.get("id"):
            tone = "ok" if status in {"succeeded", "accepted"} else "error"
            reason = error_message(data_error(data))
            self._say(f"{label}结果：{status}" + (f" · {reason}" if reason else ""), tone)
            return

        def finished(changed: bool | None) -> None:
            if changed:
                self.refresh_rows()
                self.show_detail()
                self._select_artifact(len(self.film_items) - 1)
                self._say("执行完成 · 已载入最新产物", "ok")

        self.push_screen(PlanScreen(self.deps, plan), finished)

    def action_commands(self) -> None:
        def chosen(command: str | None) -> None:
            if command is not None:
                self._open_command(command)

        self.push_screen(CommandPaletteScreen(), chosen)

    def _open_command(self, command: str) -> None:
        if self.deps.execute is None:
            self._say("执行器不可用", "error")
            return
        fields, optional = command_fields(command)
        if not fields:
            self._run_command(command, {})
            return

        def done(values: dict[str, str] | None) -> None:
            if values is None:
                return
            try:
                arguments = _convert_arguments(command, values)
            except (ValueError, TypeError) as error:
                self._say("参数错误：" + _sanitize(error), "error")
                return
            self._run_command(command, arguments)

        self.push_screen(FormScreen(f"命令 {command}", fields, optional=optional), done)

    def _run_command(self, command: str, arguments: dict[str, Any]) -> None:
        if self.deps.execute is None:
            self._say("执行器不可用", "error")
            return
        self._say(f"执行 {command}…", "info")
        self._run_command_worker(command, arguments)

    @work(thread=True, group="command", exclusive=True)
    def _run_command_worker(self, command: str, arguments: dict[str, Any]) -> None:
        try:
            status, data = _executor(self.deps)(command, arguments)
        except Exception as error:  # noqa: BLE001 - surfaced in the panel
            self.call_from_thread(self._command_failed, command, _sanitize(error))
            return
        self.call_from_thread(self._command_done, command, status, data)

    def _command_failed(self, command: str, message: str) -> None:
        self.push_screen(ResultScreen(command, "failed", {"message": message}))

    def _command_done(
        self, command: str, status: str, data: dict[str, Any],
    ) -> None:
        if data.get("plan") and status in {"approval_required", "planned"}:
            self._push_plan(status, data, "命令")
            return
        reason = error_message(data_error(data))
        self._say(
            f"{command} · {status}" + (f" · {reason}" if reason else ""),
            "ok" if status in {"succeeded", "accepted"} else "error",
        )
        self.push_screen(ResultScreen(command, status, data))
        if command.startswith(("session ", "profile ", "plan ")):
            self.refresh_rows()
            self.show_detail()

    def action_new_session(self) -> None:
        if self.deps.create_session is None:
            return

        def done(values: dict[str, str] | None) -> None:
            if not values or self.deps.create_session is None:
                return
            try:
                row = self.deps.create_session(
                    values["name"], values.get("intent", ""),
                    values.get("profile"), values.get("model"),
                )
            except sqlite3.IntegrityError:
                self._say(f"会话名已存在：{values['name']}", "error")
                return
            except Exception as error:  # noqa: BLE001 - surfaced in the panel
                self._say("新建失败：" + _sanitize(error), "error")
                return
            self.current = str(row["id"])
            self.refresh_rows()
            self.show_detail()

        self.push_screen(
            FormScreen(
                "新建会话",
                [("name", "会话名", ""), ("intent", "意图（可留空）", ""),
                 ("profile", "配置（可留空）", ""), ("model", "模型（可留空）", "")],
                optional={"intent", "profile", "model"},
            ),
            done,
        )

    def _creation_defaults(self, session: dict[str, Any]) -> tuple[str, str]:
        """Session defaults first; a single profile stands in when the session has none."""
        identity = str(session.get("default_profile_id") or "")
        if not identity:
            profiles = queries.iter_all(self.deps.repositories, "profiles")
            if len(profiles) == 1:
                identity = str(profiles[0]["id"])
        row = self.deps.repositories.get("profiles", identity) if identity else None
        model = str(session.get("default_model") or (row or {}).get("model_default") or "")
        return identity, model

    def _run_creation(self, operation: str, arguments: dict[str, Any], label: str) -> None:
        if self.deps.execute is None:
            self._say("执行器不可用", "error")
            return
        if self.planning:
            self._say("上一项仍在处理，请稍候", "info")
            return
        self.planning = True
        self._say("规划中…", "info")
        self._plan_creation(operation, arguments, label)

    @work(thread=True, group="creation", exclusive=True)
    def _plan_creation(
        self, operation: str, arguments: dict[str, Any], label: str,
    ) -> None:
        try:
            status, data = _executor(self.deps)(operation, arguments)
        except Exception as error:  # noqa: BLE001 - surfaced in the panel
            self.call_from_thread(self._plan_failed, label, _sanitize(error))
            return
        self.call_from_thread(self._plan_ready, status, data, label)

    def _plan_failed(self, label: str, message: str) -> None:
        self.planning = False
        self._say(f"{label}失败：{message}", "error")

    def _plan_ready(self, status: str, data: dict[str, Any], label: str) -> None:
        self.planning = False
        self._push_plan(status, data, label)

    def action_generate(self) -> None:
        if self.current is None:
            self._say("尚无会话；按 n 新建后再生成", "info")
            return
        session = self.deps.repositories.get("sessions", self.current)
        if session is None:
            return
        if self.deps.execute is None:
            self._say("执行器不可用", "error")
            return

        def done(values: dict[str, str] | None) -> None:
            if not values or self.deps.execute is None:
                return
            arguments: dict[str, Any] = {
                "schema_version": 1, "operation": "generate",
                "session": session["name"], "prompt": values["prompt"],
                "count": int(values.get("count", "1")),
                "size": values.get("size", "1024x1024"),
            }
            if values.get("profile"):
                arguments["profile"] = values["profile"]
            if values.get("model"):
                arguments["model"] = values["model"]
            self._run_creation("generate", arguments, "生成")

        profile_default, model_default = self._creation_defaults(session)
        self.push_screen(
            FormScreen(
                "生成图片",
                [("prompt", "提示词", ""),
                 ("profile", "配置（可留空）", profile_default),
                 ("model", "模型（可留空）", model_default),
                 ("count", "数量", "1"), ("size", "尺寸", "1024x1024")],
                optional={"profile", "model"},
            ),
            done,
        )

    def action_edit(self) -> None:
        found = self._current_artifact()
        if found is None or self.current is None:
            self._say("没有可编辑的产物", "info")
            return
        identity, _ = found
        session = self.deps.repositories.get("sessions", self.current)
        if session is None or self.deps.execute is None:
            return

        def done(values: dict[str, str] | None) -> None:
            if not values or self.deps.execute is None:
                return
            arguments: dict[str, Any] = {
                "schema_version": 1, "operation": "edit",
                "session": session["name"], "prompt": values["prompt"],
                "inputs": ["artifact:" + identity],
                "count": int(values.get("count", "1")),
                "size": values.get("size", "1024x1024"),
            }
            if values.get("profile"):
                arguments["profile"] = values["profile"]
            if values.get("model"):
                arguments["model"] = values["model"]
            self._run_creation("edit", arguments, "编辑")

        profile_default, model_default = self._creation_defaults(session)
        self.push_screen(
            FormScreen(
                f"编辑 {identity}",
                [("prompt", "提示词", ""),
                 ("profile", "配置（可留空）", profile_default),
                 ("model", "模型（可留空）", model_default),
                 ("count", "数量", "1"), ("size", "尺寸", "1024x1024")],
                optional={"profile", "model"},
            ),
            done,
        )

    def action_cache(self) -> None:
        self.push_screen(CacheScreen(self.deps))

    def action_providers(self) -> None:
        self.push_screen(ProvidersScreen(self.deps))

    def action_refresh(self) -> None:
        self.refresh_rows()
        self.show_detail()

    def _open_current(self, folder: bool) -> None:
        found = self._current_artifact()
        if found is None or self.deps.open_artifact is None:
            self._say("当前会话没有可打开的产物", "info")
            return
        identity, path = found
        try:
            status, data = self.deps.open_artifact(identity, folder)
        except (ValueError, OSError) as error:
            self._say("打开失败：" + _sanitize(error), "error")
            return
        if status == "succeeded":
            self._say(f"已打开：{data.get('path', path)}", "ok")
        else:
            self._say(f"打开结果：{status}", "error")

    def action_open_image(self) -> None:
        self._open_current(False)

    def action_open_folder(self) -> None:
        self._open_current(True)

    def action_help(self) -> None:
        self.push_screen(HelpScreen([
            ({"colon": ":"}.get(binding.key, binding.key), str(binding.description))
            for binding in self.BINDINGS
            if isinstance(binding, Binding) and binding.show
        ]))

    def action_delete(self) -> None:
        if self.current is None or self.deps.delete_session is None:
            self._say("没有可删除的会话", "info")
            return
        row = self.deps.repositories.get("sessions", self.current)
        if row is None:
            self._say("会话不存在", "error")
            return
        name = _sanitize(row["name"])

        def finished(confirmed: bool | None) -> None:
            if not confirmed or self.current is None or self.deps.delete_session is None:
                return
            try:
                self.deps.delete_session(self.deps.repositories, self.current)
            except Exception as error:
                self._say("删除失败：" + _sanitize(error), "error")
                return
            self.refresh_rows()
            self._say(f"已删除会话：{name}", "ok")

        self.push_screen(ConfirmScreen(f"输入 {name} 确认删除", name), finished)

    def action_fork(self) -> None:
        if self.current is None or self.deps.fork is None:
            return
        session = self.deps.repositories.get("sessions", self.current)
        if session is None:
            return
        turns = [
            row for row in queries.iter_all(self.deps.repositories, "turns",
                                            session_id=self.current)
            if row.get("creation_state") == "succeeded"
        ]
        if not turns:
            self._say("没有可分支的轮次", "info")
            return
        turn = sorted(turns, key=lambda row: (row.get("created_at", ""), row["id"]))[-1]
        artifacts = tuple(
            row["artifact_id"] for row in queries.iter_all(
                self.deps.repositories, "artifact_references", turn_id=turn["id"]
            )
            if row.get("role") == "output"
        )
        name = f"{session['name']}-fork"
        counter = 2
        existing = {row["name"] for row in queries.iter_all(self.deps.repositories, "sessions")}
        while name in existing:
            name = f"{session['name']}-fork-{counter}"
            counter += 1
        status, _ = self.deps.fork(
            self.current, turn_id=turn["id"], name=name, artifact_ids=artifacts
        )
        self._say(f"分支 {status}: {_sanitize(name)}",
                  "ok" if status == "succeeded" else "error")
        self.refresh_rows()

    def action_export(self) -> None:
        if self.current is None or self.deps.export is None:
            return
        session = self.deps.repositories.get("sessions", self.current)
        if session is None:
            return
        root = self.deps.workspace_root or self.deps.store.root.parent
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        default = str(root / "exports" / f"{session['name']}-{stamp}.zip")

        def done(values: dict[str, str] | None) -> None:
            if not values or self.deps.export is None:
                return
            destination = Path(values["destination"]).expanduser()
            destination.parent.mkdir(parents=True, exist_ok=True)
            status, data = self.deps.export(self.current or "", str(destination))
            self._say(
                f"导出 {status}: {_sanitize(data.get('artifacts', 0))} 个产物",
                "ok" if status == "succeeded" else "error",
            )

        self.push_screen(
            FormScreen("导出会话", [("destination", "zip 路径", default)]), done
        )

    def action_transform(self) -> None:
        if self.current is None or self.deps.transform is None:
            return
        found = self._current_artifact()
        if found is None:
            self._say("没有可变换的产物", "info")
            return
        identity, path = found

        def finished(changed: bool | None) -> None:
            if changed:
                self.show_detail()
                self._select_artifact(len(self.film_items) - 1)

        self.push_screen(
            TransformScreen(self.deps, identity, self.current, path), finished
        )
