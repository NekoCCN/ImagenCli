"""Terminal UI: browsing, confirmed writes and a local transform panel."""

from __future__ import annotations

import asyncio
import copy
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
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
from imagen_cli.application.operator_catalog import PRESET_PIPELINES as PRESETS
from imagen_cli.cli.human import render as human_render
from imagen_cli.cli.options import SharedOptions
from imagen_cli.cli.registry import COMMANDS, discover
from imagen_cli.cli.renderers import data_error, error_message
from imagen_cli.storage import queries
from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.private_state import PrivateStateStore
from imagen_cli.storage.repositories import Repositories

from . import preview


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
        self,
        session_id: str,
        *,
        turn_id: str,
        name: str,
        artifact_ids: tuple[str, ...],
    ) -> tuple[str, dict[str, Any]]:
        from imagen_cli.cli.commands import history

        return history.fork_(
            self.repositories,
            session_id,
            turn_id=turn_id,
            name=name,
            artifact_ids=artifact_ids,
        )

    def _export(self, session_id: str, destination: str) -> tuple[str, dict[str, Any]]:
        from imagen_cli.cli.commands import history

        return history.export_(self.repositories, self.store, session_id, destination)

    def _transform(
        self,
        artifact: str,
        pipeline: list[dict[str, Any]],
        session: str,
    ) -> dict[str, Any]:
        from imagen_cli.application import postprocess_service

        return postprocess_service.apply(
            self.repositories,
            self.store,
            artifact,
            pipeline,
            session_id=session,
        )

    def _profile_set(self, parameters: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        from imagen_cli.cli.commands import profile as profile_command

        data = profile_command.build_set(parameters)
        return profile_command.execute(
            self.repositories,
            "profile set",
            profile=parameters["profile"],
            data=data,
        )

    def _models_refresh(self, profile: str) -> tuple[str, dict[str, Any]]:
        from functools import partial

        from imagen_cli.cli.commands import models
        from imagen_cli.execution.configured import resolve_credential

        return models.refresh_(
            self.repositories,
            profile,
            send_credential=True,
            resolve_credential=partial(resolve_credential, private_store=self.private_store),
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
            "artifact": artifact,
            "path": str(path.parent if folder else path),
            "opened": "folder" if folder else "image",
        }

    def _secret_ref(self, profile: str, key: str) -> str:
        if self.private_store is None:
            raise ValueError("private credential storage is unavailable")
        return self.private_store.write_credential(profile, key)

    def _create_session(
        self,
        name: str,
        intent: str = "",
        profile: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        from imagen_cli.application.session_service import SessionService

        row = SessionService(self.repositories, self.store).create_session(
            name,
            intent=intent,
            default_profile_id=profile,
            default_model=model,
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
            classes="panel",
            id="confirm",
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

    def __init__(
        self,
        title: str,
        fields: list[tuple[str, str, str]],
        optional: set[str] | None = None,
        secrets: set[str] | None = None,
    ) -> None:
        super().__init__()
        self.heading = title
        self.fields = fields
        self.optional = optional or set()
        self.secrets = secrets or set()
        self.inputs = [
            Input(value=default, placeholder=placeholder, id=name, password=name in self.secrets)
            for name, placeholder, default in fields
        ]

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(self.heading, classes="panel-title"),
            *(
                Horizontal(Static(_label(name), classes="field-label"), field, classes="field")
                for (name, _, _), field in zip(self.fields, self.inputs, strict=True)
            ),
            Static("enter 提交 · esc 取消", classes="panel-hint"),
            classes="panel",
            id="form",
        )

    def on_mount(self) -> None:
        if self.inputs:
            self.inputs[0].focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        values = {str(field.id): field.value.strip() for field in self.inputs}
        missing = [
            name for name, value in values.items() if not value and name not in self.optional
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
            self.table,
            self.status,
            self.field,
            classes="panel",
            id="cache",
        )

    def on_mount(self) -> None:
        self.table.cursor_type = "row"
        self.table.add_columns("类型", "条目", "字节")
        extra = (
            {} if self.deps.private_store is None else {"private_store": self.deps.private_store}
        )
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
        extra = (
            {} if self.deps.private_store is None else {"private_store": self.deps.private_store}
        )
        result = self.deps.purge(self.deps.repositories, self.deps.store, self.digest, **extra)
        self.status.update(f"已删除 {result['deleted_bytes']} 字节")
        self.dismiss(True)

    def action_dismiss_screen(self) -> None:
        self.dismiss(False)


class ParamEditScreen(ModalScreen[list[dict[str, Any]] | None]):
    """Adjust parameters for the current transform pipeline."""

    BINDINGS = [
        Binding("tab", "toggle_mode", "切换模式"),
        Binding("m", "toggle_mode", "切换模式"),
        Binding("escape", "cancel", "取消"),
    ]

    def __init__(self, preset_name: str, pipeline: list[dict[str, Any]]) -> None:
        super().__init__()
        from imagen_cli.application.operator_catalog import get_operator_item

        self.preset_name = preset_name
        self.pipeline = copy.deepcopy(pipeline)
        self.mode: str = "basic"
        self.fields_info: list[tuple[str, str, str, str, int, str, bool]] = []
        self.inputs: list[Input] = []

        for step_idx, step in enumerate(self.pipeline):
            op = step.get("op", "")
            params = step.get("params", {})
            catalog_item = get_operator_item(op)
            catalog_params = {p.name: p for p in catalog_item.parameters} if catalog_item else {}

            all_param_names = list(params.keys())
            if catalog_item:
                for p in catalog_item.parameters:
                    if p.name not in params:
                        all_param_names.append(p.name)
                        params[p.name] = p.default

            for k in all_param_names:
                v = params.get(k)
                field_id = f"f_{step_idx}_{k}"
                p_meta = catalog_params.get(k)
                is_adv = p_meta.is_advanced if p_meta else False

                if p_meta:
                    prefix = "[高级] " if is_adv else ""
                    label = f"{prefix}{p_meta.label} ({k})"
                    placeholder = f"默认 {p_meta.default} · {p_meta.description}"
                    expected_type = p_meta.param_type
                    if expected_type == "choice" and type(p_meta.default) is int:
                        expected_type = "int"
                else:
                    label = f"{op}.{k}"
                    placeholder = f"当前值: {v}"
                    expected_type = type(v).__name__

                self.fields_info.append(
                    (field_id, label, placeholder, expected_type, step_idx, k, is_adv)
                )
                self.inputs.append(
                    Input(
                        value=str(v) if v is not None else "",
                        placeholder=placeholder,
                        id=field_id,
                    )
                )

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel", id="param-form"):
            yield Static(f"参数调节 · {self.preset_name}", classes="panel-title")
            yield Static("", id="param-mode-indicator")
            for (field_id, label, _, _, _, _, _), inp in zip(
                self.fields_info, self.inputs, strict=True
            ):
                with Horizontal(classes="field", id=f"c_{field_id}"):
                    yield Static(label, classes="field-label")
                    yield inp
            yield Static("enter 确认并预览 · Tab/m 切换基础/高级 · esc 取消", classes="panel-hint")

    def on_mount(self) -> None:
        self._update_mode_view()

    def _update_mode_view(self) -> None:
        if self.mode == "basic":
            indicator_text = (
                "模式: [bold cyan]● 基础浓缩参数[/]   [dim]○ 全部高级参数[/]  (Tab/m 切换)"
            )
        else:
            indicator_text = (
                "模式: [dim]○ 基础浓缩参数[/]   [bold cyan]● 全部高级参数[/]  (Tab/m 切换)"
            )
        self.query_one("#param-mode-indicator", Static).update(indicator_text)

        first_visible: Input | None = None
        for (field_id, _, _, _, _, _, is_adv), inp in zip(
            self.fields_info, self.inputs, strict=True
        ):
            container = self.query_one(f"#c_{field_id}", Horizontal)
            is_visible = not is_adv if self.mode == "basic" else True
            container.display = is_visible
            if is_visible and first_visible is None:
                first_visible = inp

        if first_visible:
            first_visible.focus()

    def action_toggle_mode(self) -> None:
        self.mode = "advanced" if self.mode == "basic" else "basic"
        self._update_mode_view()

    def _save_inputs_to_pipeline(self) -> list[dict[str, Any]]:
        new_pipeline = copy.deepcopy(self.pipeline)
        for (_, _, _, type_str, step_idx, k, _), inp in zip(
            self.fields_info, self.inputs, strict=True
        ):
            raw = inp.value.strip()
            if not raw:
                continue
            try:
                if type_str in ("bool", "boolean"):
                    val: Any = raw.lower() in ("true", "1", "yes", "on")
                elif type_str in ("int", "integer"):
                    val = int(raw)
                elif type_str in ("float", "number"):
                    val = float(raw)
                else:
                    val = raw
                new_pipeline[step_idx]["params"][k] = val
            except (ValueError, TypeError):
                pass
        return new_pipeline

    def on_input_submitted(self, event: Input.Submitted) -> None:
        new_pipeline = self._save_inputs_to_pipeline()
        self.dismiss(new_pipeline)

    def action_cancel(self) -> None:
        self.dismiss(None)


class TransformScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("up", "cursor_up", "上一个", show=False),
        Binding("down", "cursor_down", "下一个", show=False),
        Binding("c", "toggle_category", "切换分类"),
        Binding("tab", "toggle_category", "切换分类", show=False),
        Binding("p", "edit_params", "参数调节"),
        Binding("enter", "apply", "应用"),
        Binding("escape", "cancel", "取消"),
    ]

    def __init__(self, deps: Deps, artifact: str, session: str, path: Path) -> None:
        super().__init__()
        self.deps = deps
        self.artifact = artifact
        self.session = session
        self.path = path
        self.running = False
        self.cancelled = Event()
        from imagen_cli.application.operator_catalog import browse_entries

        self.entries = {entry["key"]: entry for entry in browse_entries()}
        self.categories = ["全部", "综合配方", "平滑与缺陷", "线条与色块", "颜色明暗",
                           "风格光学", "模型恢复", "尺寸几何"]
        self.all_names = list(self.entries)
        self.filtered_names = list(self.all_names)
        self.names = self.filtered_names
        self.current_category_idx = 0
        self.pipelines: dict[str, list[dict[str, Any]]] = {
            k: [dict(step, params=dict(step.get("params", {}))) for step in v]
            for k, v in PRESETS.items()
        }
        self.category_table: DataTable[Any] = DataTable(id="transform-categories")
        self.table: DataTable[Any] = DataTable(id="transform-presets")
        self.status = Static("")
        self.view = Static("")
        self.intro_view = Static("", id="transform-intro-view")
        self.params_view = Static("", id="transform-params-view")
        self.header_title = Static(
            "变换预设与后处理工作室（实验性 experimental）", classes="panel-title",
        )

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel", id="transform"):
            yield self.header_title
            with Horizontal():
                with Vertical(id="transform-cat-col"):
                    yield self.category_table
                with Vertical(id="transform-preset-col"):
                    yield self.table
                with Vertical(id="transform-preview-col"):
                    yield self.view
                    yield self.intro_view
                    yield self.params_view
                    yield self.status
            yield Static(
                "↑↓ 预设 · c/Tab 分类 · p 调参 · enter 应用 · esc 取消", classes="panel-hint"
            )

    def on_mount(self) -> None:
        self.category_table.cursor_type = "row"
        self.category_table.add_columns("分类")
        for cat in self.categories:
            self.category_table.add_row(cat)
        self.category_table.can_focus = False
        self.category_table.move_cursor(row=0)

        self.table.cursor_type = "row"
        self.table.add_columns("预设")
        self._populate_table()
        self.table.can_focus = False
        self.table.move_cursor(row=0)
        self.set_focus(None)
        self.render_preview()

    def _populate_table(self) -> None:
        self.table.clear()
        for name in self.filtered_names:
            self.table.add_row(self.entries[name]["name"])

    def action_toggle_category(self) -> None:
        self.current_category_idx = (self.current_category_idx + 1) % len(self.categories)
        self.category_table.move_cursor(row=self.current_category_idx)
        cat_filter = self.categories[self.current_category_idx]
        if cat_filter == "全部":
            self.filtered_names = list(self.all_names)
            self.header_title.update("变换预设与后处理工作室（实验性 experimental）")
        else:
            self.filtered_names = [
                n for n in self.all_names if self.entries[n]["group"] == cat_filter
            ]
            self.header_title.update(
                f"变换预设与后处理工作室（实验性 experimental）  [dim cyan]({cat_filter})[/]"
            )
        self.names = self.filtered_names
        self._populate_table()
        if self.names:
            self.table.move_cursor(row=0)
            self.render_preview()
        else:
            self.view.update("")
            self.intro_view.update("[dim]该分类下无匹配预设[/]")
            self.params_view.update("")

    def action_cursor_up(self) -> None:
        self._move(-1)

    def action_cursor_down(self) -> None:
        self._move(1)

    def _move(self, delta: int) -> None:
        if not self.names:
            return
        row = min(len(self.names) - 1, max(0, self.table.cursor_row + delta))
        self.table.move_cursor(row=row)
        self.render_preview()

    def _describe_pipeline(self, pipeline: list[dict[str, Any]]) -> str:
        parts = []
        for step in pipeline:
            op = step.get("op", "")
            params = step.get("params", {})
            if params:
                param_str = ", ".join(f"{k}={v}" for k, v in params.items())
                parts.append(f"• {op}: {param_str}")
            else:
                parts.append(f"• {op}")
        return "\n".join(parts)

    def render_preview(self) -> None:
        row = self.table.cursor_row
        if not 0 <= row < len(self.names):
            return
        name = self.names[row]
        pipeline = self.pipelines.get(name, PRESETS[name])
        from imagen_cli.application import operator_catalog

        intro_text = operator_catalog.get_intro_text(name)
        self.intro_view.update(Text.from_markup(intro_text))
        try:
            with Image.open(self.path) as opened:
                opened.load()
                thumbnail = opened.convert("RGBA" if "A" in opened.getbands() else "RGB")
                thumbnail.thumbnail((48, 24))
                # 若算子需要 alpha 通道而当前图像无透明层，为缩略图自适应透明边缘以支持预览
                needs_alpha = any(
                    s.get("op") in ("sticker_outline", "background_blur") for s in pipeline
                )
                if needs_alpha and "A" not in thumbnail.getbands():
                    from PIL import ImageDraw

                    mask = Image.new("L", thumbnail.size, 0)
                    draw = ImageDraw.Draw(mask)
                    draw.rectangle(
                        [2, 1, max(3, thumbnail.width - 3), max(2, thumbnail.height - 2)], fill=255
                    )
                    thumbnail = thumbnail.convert("RGBA")
                    thumbnail.putalpha(mask)
                for step in pipeline:
                    op_name = step.get("op", "")
                    if op_name == "superres":
                        model_name = str(step.get("params", {}).get("model", ""))
                        scale = 4 if "x4" in model_name else 2
                        target_w = min(48, max(thumbnail.width, thumbnail.width * scale))
                        target_h = min(24, max(thumbnail.height, thumbnail.height * scale))
                        thumbnail = thumbnail.resize((target_w, target_h), Image.Resampling.LANCZOS)
                        continue
                    if op_name in media_ops.OPS:
                        thumbnail = media_ops.OPS[op_name](thumbnail, **step.get("params", {}))
            image = thumbnail.convert("RGB")
            self.view.update(Text.from_ansi(preview.ansi_image(image, cols=48, rows=12)))
            self.params_view.update(self._describe_pipeline(pipeline))
            self.status.update(f"{name} · p 调参 · enter 应用")
        except (ValueError, KeyError, OSError, RuntimeError) as error:
            self.view.update("预览不可用")
            self.params_view.update(self._describe_pipeline(pipeline))
            self.status.update(_sanitize(error))

    def action_edit_params(self) -> None:
        row = self.table.cursor_row
        if not 0 <= row < len(self.names):
            return
        name = self.names[row]
        pipeline = self.pipelines.get(name, PRESETS[name])
        from imagen_cli.application.operator_catalog import get_operator_item

        def _step_has_params(step: dict[str, Any]) -> bool:
            if step.get("params"):
                return True
            item = get_operator_item(step.get("op", ""))
            return bool(item.parameters) if item is not None else False

        has_params = any(_step_has_params(s) for s in pipeline)
        if not has_params:
            self.status.update(f"{name} 无可配置参数")
            return

        def on_saved(updated_pipeline: list[dict[str, Any]] | None) -> None:
            if updated_pipeline is not None:
                self.pipelines[name] = updated_pipeline
                self.render_preview()
                self.status.update(f"{name} 参数已更新 · enter 应用")

        self.app.push_screen(ParamEditScreen(name, pipeline), on_saved)

    def action_apply(self) -> None:
        if self.running:
            return
        row = self.table.cursor_row
        if not 0 <= row < len(self.names):
            return
        name = self.names[row]
        assert self.deps.transform is not None
        pipeline = self.pipelines.get(name, PRESETS[name])
        if self.deps.transform == self.deps._transform:
            self.running = True
            self.cancelled.clear()
            self.status.update("处理中 · Esc 取消，成功后保存新产物")
            self.run_worker(self._apply_isolated(pipeline), exclusive=True)
            return
        try:
            result = self.deps.transform(self.artifact, pipeline, self.session)
        except (ValueError, KeyError, OSError, RuntimeError) as error:
            self.status.update(_sanitize(error))
            return
        self.dismiss(bool(result))

    async def _apply_isolated(self, pipeline: list[dict[str, Any]]) -> None:
        from imagen_cli.application import postprocess_service
        from imagen_cli.application.postprocess_worker import execute

        try:
            # Only file computation leaves the UI thread; SQLite publication stays here.
            steps = postprocess_service.validate(pipeline)
            data, metadata = await asyncio.to_thread(
                execute, self.path, steps, cancelled=self.cancelled,
            )
            if self.cancelled.is_set():
                self.status.update("处理已取消，未保存产物")
                return
            result = postprocess_service.publish_result(
                self.deps.repositories, self.deps.store, self.artifact, steps,
                data, metadata, session_id=self.session,
            )
        except (ValueError, KeyError, OSError, RuntimeError) as error:
            self.status.update(_sanitize(error))
        else:
            self.dismiss(bool(result))
        finally:
            self.running = False

    def on_unmount(self) -> None:
        self.cancelled.set()

    def action_cancel(self) -> None:
        if self.running:
            self.cancelled.set()
            self.status.update("正在取消处理…")
            return
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
            self.filter,
            self.table,
            Static("输入过滤 · enter 选定 · esc 取消", classes="panel-hint"),
            classes="panel",
            id="model-picker",
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
        ("credential_ref", "env:NAME / keyring:SERVICE / private:NAME（可留空）", "env:IMAGEN_KEY"),
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
            self.table,
            self.status,
            classes="panel",
            id="providers",
        )

    def on_mount(self) -> None:
        self.table.cursor_type = "row"
        self.table.add_columns("id", "provider", "family", "model", "revision")
        self.refresh_rows()

    def refresh_rows(self) -> None:
        self.table.clear()
        for row in queries.iter_all(self.deps.repositories, "profiles"):
            self.table.add_row(
                _sanitize(row["id"]),
                _sanitize(row["provider"]),
                _sanitize(row["family"]),
                _sanitize(row["model_default"]),
                str(row["revision"]),
                key=row["id"],
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
            parameters["credential_ref"] = self.deps.secret_ref(str(values["profile"]), secret)
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
                "新增 profile",
                list(self.FIELDS),
                optional=self.OPTIONAL,
                secrets=self.SECRETS,
            ),
            done,
        )

    def action_update(self) -> None:
        profile = self._selected()
        if profile is None:
            self.status.update("没有选中 profile")
            return
        defaults = [
            (
                name,
                placeholder,
                _sanitize(profile["id"] if name == "profile" else profile.get(name, value)),
            )
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
                "更新 profile",
                defaults,
                optional=self.OPTIONAL,
                secrets=self.SECRETS,
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

        self.app.push_screen(FormScreen("添加模型", [("model", "model id", "")]), done)

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

        self.app.push_screen(ModelPickerScreen(rows, str(profile.get("model_default") or "")), done)

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
            classes="panel",
            id="plan",
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
        self.app.run_worker(self._execute_job, thread=True, group="plan-execute", exclusive=True)

    def _execute_job(self) -> None:
        try:
            status, data = _executor(self.deps)("plan execute", {"plan_id": self.plan["id"]})
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
            kind = next((item.get("type") for item in spec["anyOf"] if item.get("type")), None)
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
                arguments[name] = [item.strip() for item in raw.split(",") if item.strip()]
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
            classes="panel",
            id="result",
        )

    def on_mount(self) -> None:
        ok = self.status in {"succeeded", "accepted", "approved", "planned"}
        message = error_message(data_error(self.data)) or str(
            self.data.get("message") or self.status
        )
        value = {
            "ok": ok,
            "status": self.status,
            "data": self.data,
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
            name
            for name, command in COMMANDS.items()
            if command.available and name not in {"tui", "help", "schema"}
        )
        self.matches = list(self.names)
        self.table: DataTable[Any] = DataTable()
        self.filter: Input = Input(placeholder="过滤命令或说明", id="palette-filter")
        self.status = Static("")

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("全部命令", classes="panel-title"),
            self.filter,
            self.table,
            self.status,
            Static("输入过滤 · ↑↓ 选择 · enter 打开 · esc 关闭", classes="panel-hint"),
            classes="panel",
            id="palette",
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
            name
            for name in self.names
            if not needle or needle in name.lower() or needle in str(COMMANDS[name].summary).lower()
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
            kind = next((item.get("type") for item in spec["anyOf"] if item.get("type")), None)
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
            classes="panel",
            id="help",
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
    #transform { width: 92%; max-width: 114; height: 90%; overflow: hidden; }
    #transform-cat-col { width: 14; height: 1fr; border-right: solid #252b36; }
    #transform-preset-col { width: 26; height: 1fr; margin-left: 1; }
    #transform-categories, #transform-presets { height: 1fr; width: 1fr; }
    #transform-preview-col { width: 1fr; margin-left: 2; height: 1fr; }
    #transform-intro-view {
        height: auto; max-height: 5; overflow-y: auto; color: #ddd8d4;
        margin: 1 0; padding: 0 1; background: #0f131a; border-left: solid #f2a6c0;
    }
    #transform-params-view { height: 4; overflow-y: auto; color: #8e97a4; margin-top: 1; }
    #param-form { max-width: 82; max-height: 84%; overflow-y: auto; }
    #param-form .field-label { width: 24; }
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
        Binding("d", "delete_artifact", "删除图片"),
        Binding("delete", "delete_artifact", "删除图片", show=False),
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
        self._preview_cache: dict[tuple[str, int, int, float], str] = {}
        self._preview_request_id: int = 0

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
        yield Static(
            _keybar(
                [
                    ("g", "生成"),
                    ("m", "编辑"),
                    ("t", "变换"),
                    ("d", "删图"),
                    ("x", "删除会话"),
                    ("[ ]", "产物"),
                    (":", "命令"),
                    ("?", "帮助"),
                ]
            ),
            id="keybar",
        )

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
            row
            for row in queries.iter_all(self.deps.repositories, "sessions")
            if row.get("deleted_at") is None
        ]
        for row in sessions:
            turns = len(queries.iter_all(self.deps.repositories, "turns", session_id=row["id"]))
            self.sessions.add_row(
                _sanitize(row["name"]),
                str(turns),
                str(row["version"]),
                key=row["id"],
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
        if (
            self.sessions.row_count == 0
            or event.row_key
            != self.sessions.coordinate_to_cell_key(self.sessions.cursor_coordinate).row_key
        ):
            return
        identity = str(event.row_key.value)
        if identity != self.current:
            self.current = identity
            self._say("")
        self.show_detail()

    def on_resize(self) -> None:
        self.show_detail()

    def _artifact_item(
        self,
        identity: str,
        turn: dict[str, Any] | None,
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
                queries.iter_all(self.deps.repositories, "artifact_references", turn_id=turn["id"]),
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
        self._select_artifact(
            (self.film_index if self.film_index >= 0 else len(self.film_items)) - 1
        )

    def action_next_artifact(self) -> None:
        self._select_artifact(
            (self.film_index if self.film_index >= 0 else len(self.film_items) - 1) + 1
        )

    def _render_film(self) -> None:
        if not self.film_items:
            self.film.update("")
            return
        # Keep a window around the selection so a long session stays one line.
        width = self.film.size.width or 0
        limit = max(3, min(9, (width - 14) // 5)) if width else 9
        start = max(0, min(len(self.film_items) - limit, self.film_index - limit // 2))
        window = self.film_items[start : start + limit]
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
        if not preview.supported():
            self.preview.update(preview.describe(artifact, path))
            return

        frame = self.query_one("#preview-frame")
        cols = max(20, frame.size.width - 2)
        rows = max(5, frame.size.height - 2)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0

        cache_key = (str(path), cols, rows, mtime)
        if cache_key in self._preview_cache:
            self.preview.update(Text.from_ansi(self._preview_cache[cache_key]))
            return

        self._preview_request_id += 1
        req_id = self._preview_request_id
        # 轻量占位符，UI 立即响应
        self.preview.update(Text(f"\n\n正在加载图像预览…\n{size}", justify="center"))
        self._load_preview_async(req_id, path, cols, rows, mtime)

    @work(thread=True, group="preview_render", exclusive=True)
    def _load_preview_async(
        self, req_id: int, path: Path, cols: int, rows: int, mtime: float
    ) -> None:
        try:
            ansi_str = preview.ansi_blocks(path, cols=cols, rows=rows)
        except Exception:
            return
        self.call_from_thread(self._on_preview_loaded, req_id, ansi_str, path, cols, rows, mtime)

    def _on_preview_loaded(
        self, req_id: int, ansi_str: str, path: Path, cols: int, rows: int, mtime: float
    ) -> None:
        if req_id != self._preview_request_id:
            return
        cache_key = (str(path), cols, rows, mtime)
        if len(self._preview_cache) > 128:
            self._preview_cache.clear()
        self._preview_cache[cache_key] = ansi_str
        self.preview.update(Text.from_ansi(ansi_str))

    def show_detail(self) -> None:
        if self.current is None:
            return
        row = self.deps.repositories.get("sessions", self.current)
        if row is None:
            return
        self.detail.update(f"{_sanitize(row['name'])} · 版本 {row['version']}")
        profile, model = self._creation_defaults(row)
        model = model.rsplit("/", 1)[-1]
        self.meta_base = (
            " · ".join(part for part in (profile, model) if part) or "尚未选择端点与模型"
        )
        self.meta.update(self.meta_base)
        previous = (
            str(self.film_items[self.film_index]["id"])
            if 0 <= self.film_index < len(self.film_items)
            else None
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
        required = [f for f in fields if f[0] not in optional]
        if not required:
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
        self,
        command: str,
        status: str,
        data: dict[str, Any],
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
                    values["name"],
                    values.get("intent", ""),
                    values.get("profile"),
                    values.get("model"),
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
                [
                    ("name", "会话名", ""),
                    ("intent", "意图（可留空）", ""),
                    ("profile", "配置（可留空）", ""),
                    ("model", "模型（可留空）", ""),
                ],
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
        self,
        operation: str,
        arguments: dict[str, Any],
        label: str,
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
                "schema_version": 1,
                "operation": "generate",
                "session": session["name"],
                "prompt": values["prompt"],
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
                [
                    ("prompt", "提示词", ""),
                    ("profile", "配置（可留空）", profile_default),
                    ("model", "模型（可留空）", model_default),
                    ("count", "数量", "1"),
                    ("size", "尺寸", "1024x1024"),
                ],
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
                "schema_version": 1,
                "operation": "edit",
                "session": session["name"],
                "prompt": values["prompt"],
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
                [
                    ("prompt", "提示词", ""),
                    ("profile", "配置（可留空）", profile_default),
                    ("model", "模型（可留空）", model_default),
                    ("count", "数量", "1"),
                    ("size", "尺寸", "1024x1024"),
                ],
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
        self.push_screen(
            HelpScreen(
                [
                    ({"colon": ":"}.get(binding.key, binding.key), str(binding.description))
                    for binding in self.BINDINGS
                    if isinstance(binding, Binding) and binding.show
                ]
            )
        )

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

    def action_delete_artifact(self) -> None:
        target = self._current_artifact()
        if target is None or self.current is None:
            self._say("当前会话没有选中的图片", "info")
            return
        art_id, _ = target
        num = self.film_index + 1
        total = len(self.film_items)

        def finished(confirmed: bool | None) -> None:
            if not confirmed or self.current is None:
                return
            try:
                from imagen_cli.application.cleanup_service import remove_session_artifact

                remove_session_artifact(
                    self.deps.repositories, self.deps.store, self.current, art_id, purge=True
                )
            except Exception as error:
                self._say("删除图片失败：" + _sanitize(error), "error")
                return
            self.refresh_rows()
            self.show_detail()
            self._say(f"已从会话中移除图片 {art_id}", "ok")

        self.push_screen(
            ConfirmScreen(f"确认从当前会话中移除第 {num}/{total} 张图片？输入 yes 确认", "yes"),
            finished,
        )

    def action_fork(self) -> None:
        if self.current is None or self.deps.fork is None:
            return
        session = self.deps.repositories.get("sessions", self.current)
        if session is None:
            return
        turns = [
            row
            for row in queries.iter_all(self.deps.repositories, "turns", session_id=self.current)
            if row.get("creation_state") == "succeeded"
        ]
        if not turns:
            self._say("没有可分支的轮次", "info")
            return
        turn = sorted(turns, key=lambda row: (row.get("created_at", ""), row["id"]))[-1]
        artifacts = tuple(
            row["artifact_id"]
            for row in queries.iter_all(
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
        self._say(f"分支 {status}: {_sanitize(name)}", "ok" if status == "succeeded" else "error")
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

        self.push_screen(FormScreen("导出会话", [("destination", "zip 路径", default)]), done)

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

        self.push_screen(TransformScreen(self.deps, identity, self.current, path), finished)
