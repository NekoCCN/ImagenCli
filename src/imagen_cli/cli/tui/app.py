"""Terminal UI: browsing, confirmed writes and a local transform panel."""

from __future__ import annotations

import asyncio
import copy
import math
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any, cast

from PIL import Image
from rich.cells import cell_len
from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.theme import Theme
from textual.widgets import Button, DataTable, Input, Select, Static

from imagen_cli.application import cache_service, media_ops, superres
from imagen_cli.application.operator_catalog import PRESET_PIPELINES as PRESETS
from imagen_cli.application.skill_targets import prompt_order
from imagen_cli.cli.human import render as human_render
from imagen_cli.cli.options import COMMAND_CONTROLS, SharedOptions
from imagen_cli.cli.registry import COMMANDS, command_summary, discover
from imagen_cli.cli.renderers import data_error, error_message
from imagen_cli.i18n import is_chinese, t
from imagen_cli.providers import vocabulary
from imagen_cli.storage import queries
from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.private_state import PrivateStateStore
from imagen_cli.storage.repositories import Repositories

from . import preview
from .forms import ChoiceForm, CreationForm, ModelUseForm, ProviderForm, SheetForm
from .modal import Modal


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


def _executor(
    deps: Deps, options: SharedOptions | None = None
) -> Callable[[str, dict[str, Any]], tuple[str, dict[str, Any]]]:
    """Workers get their own connection; the app's connection stays on the UI thread."""
    assert deps.execute is not None
    if deps.workspace_root is None:
        return deps.execute
    root = str(deps.workspace_root)
    options = options or deps.options or SharedOptions()

    def run(command: str, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        from imagen_cli.application.container import Container

        with Container(workspace=root, options=options) as container:
            return container.execute(command, arguments)

    return run


_TONES = {
    "muted": "#b0b7c2",
    "info": "#f2c879",
    "ok": "#81c995",
    "error": "#ed8b8b",
}

# Schema field names stay the machine contract; only the visible label is translated.
_FIELD_LABELS = {
    "prompt": ("Prompt", "提示词"),
    "profile": ("Profile", "配置"),
    "model": ("Model", "模型"),
    "count": ("Count", "数量"),
    "size": ("Size", "尺寸"),
    "name": ("Name", "名称"),
    "intent": ("Intent", "意图"),
    "session": ("Session", "会话"),
    "operation": ("Operation", "操作"),
    "artifact": ("Artifact", "产物"),
    "artifact_id": ("Artifact", "产物"),
    "plan_id": ("Plan", "计划"),
    "destination": ("Destination path", "目标路径"),
    "provider": ("Provider", "供应商"),
    "family": ("Endpoint type", "端点类型"),
    "base_url": ("Endpoint", "端点"),
    "model_default": ("Default model", "默认模型"),
    "credential_ref": ("Credential reference", "凭证引用"),
    "api_key": ("API key", "API key"),
    "revision": ("Revision", "修订"),
    "limit": ("Limit", "条数"),
    "cursor": ("Cursor", "游标"),
    "digest": ("Digest", "摘要"),
    "pipeline": ("Pipeline", "流水线"),
    "op": ("Operation", "操作"),
    "archive": ("Archive path", "归档路径"),
    "dry_run": ("Dry run", "仅演示"),
    "yes": ("Confirm", "确认"),
    "folder": ("Folder", "目录"),
    "send_credential": ("Send credential", "发送凭证"),
    "timeout": ("Timeout", "超时"),
    "wait": ("Wait for completion", "等待完成"),
    "async": ("Async", "异步"),
    "async_": ("Async", "异步"),
    "concurrency": ("Concurrency", "并发"),
    "skill": ("Skill", "技能"),
    "scope": ("Scope", "范围"),
    "force": ("Overwrite", "覆盖"),
}


def _label(name: str) -> str:
    labels = _FIELD_LABELS.get(name)
    return t(*labels) if labels else name


_KEYBAR_KEYS: list[tuple[str, tuple[str, str]]] = [
    ("g", ("Generate", "生成")),
    ("m", ("Edit", "编辑")),
    ("t", ("Transform", "变换")),
    ("o", ("Open", "打开")),
    ("d", ("Delete", "删图")),
    ("x", ("Delete session", "删除会话")),
    ("[ ]", ("Artifacts", "产物")),
    ("v", ("Providers", "端点")),
    ("s", ("Local models", "本地模型")),
    ("n", ("New", "新建")),
    ("e", ("Export", "导出")),
    ("c", ("Cache", "缓存")),
    ("f", ("Fork", "分支")),
    ("r", ("Refresh", "刷新")),
    ("l", ("Language", "语言")),
    ("q", ("Quit", "退出")),
]
_KEYBAR_TAIL = [(":", ("Commands", "命令")), ("?", ("Help", "帮助"))]
_KEYBAR_EMPTY_WORKSPACE = {"n", "v", "s", "c", "l", "q"}


def _ellipsize(value: str, width: int) -> str:
    """Cut a cell to its column so a clipped name reads as clipped, not as a typo."""
    if cell_len(value) <= width:
        return value
    kept = value
    while kept and cell_len(kept) + 1 > width:
        kept = kept[:-1]
    return kept + "…"


def _keybar_width(key: str, label: str) -> int:
    """Cells one legend entry takes, key badge included."""
    return cell_len(f" {key} ") + cell_len(f" {label}")


def _keybar(entries: list[tuple[str, str]], width: int) -> Text:
    """Fit the legend to the terminal: commands and help stay, the rest is cut from the end."""
    text = Text()
    if not entries:
        return text
    # One separator between entries, and the tail always keeps its slot.
    tail_width = sum(_keybar_width(key, label) + 1 for key, label in entries[-2:])
    budget = max(0, width - tail_width)
    shown: list[tuple[str, str]] = []
    used = 0
    for key, label in entries[:-2]:
        cost = _keybar_width(key, label) + 1
        if used + cost > budget:
            break
        used += cost
        shown.append((key, label))
    for key, label in shown + entries[-2:]:
        if text:
            text.append(" ")
        text.append(f" {key} ", style="bold #24131b on #e98cab")
        text.append(f" {label}", style="#9aa2af")
    return text


class ConfirmScreen(Modal[bool]):
    """Yes/No dialog with No selected first; Enter confirms the highlighted choice."""

    BINDINGS = [
        Binding("left", "select_no", "", show=False),
        Binding("right", "select_yes", "", show=False),
        Binding("h", "select_no", "", show=False),
        Binding("l", "select_yes", "", show=False),
        Binding("enter", "confirm", t("Confirm", "确认")),
        Binding("n", "answer_no", t("No", "否")),
        Binding("y", "answer_yes", t("Yes", "是")),
        Binding("escape", "cancel", t("Cancel", "取消")),
    ]

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self.prompt = prompt
        self.yes = False
        self.choice = Static("")

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(self.prompt, classes="panel-title"),
            self.choice,
            Horizontal(
                Button(t("No", "否"), id="confirm-no"),
                Button(t("Yes", "是"), id="confirm-yes", variant="error"),
                classes="dialog-actions",
            ),
            Static(
                t(
                    "←/→ choose · enter confirm · esc cancels",
                    "←/→ 选择 · enter 确认 · esc 取消",
                ),
                classes="panel-hint",
            ),
            classes="panel",
            id="confirm",
        )

    def on_mount(self) -> None:
        self._render_choice()

    def _render_choice(self) -> None:
        no = Text(t("No", "否"), style="reverse" if not self.yes else "")
        yes = Text(t("Yes", "是"), style="reverse" if self.yes else "")
        self.choice.update(Text.assemble(no, "    ", yes))
        self.query_one("#confirm-yes" if self.yes else "#confirm-no", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "confirm-yes")

    def action_select_no(self) -> None:
        self.yes = False
        self._render_choice()

    def action_select_yes(self) -> None:
        self.yes = True
        self._render_choice()

    def action_confirm(self) -> None:
        self.dismiss(self.yes)

    def action_answer_no(self) -> None:
        self.dismiss(False)

    def action_answer_yes(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class FormScreen(Modal[dict[str, str] | None]):
    """Small labelled form; Enter submits, Escape cancels."""

    BINDINGS = [Binding("escape", "cancel", t("Cancel", "取消"))]

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
            Static(t("enter submit · esc cancel", "enter 提交 · esc 取消"), classes="panel-hint"),
            Horizontal(
                Button(t("Submit", "提交"), id="form-submit", variant="primary"),
                Button(t("Cancel", "取消"), id="form-cancel"),
                classes="dialog-actions",
            ),
            classes="panel",
            id="form",
        )

    def on_mount(self) -> None:
        if self.inputs:
            self.inputs[0].focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "form-submit":
            self._submit()
        elif event.button.id == "form-cancel":
            self.action_cancel()

    def _submit(self) -> None:
        values = {str(field.id): field.value.strip() for field in self.inputs}
        missing = [
            name for name, value in values.items() if not value and name not in self.optional
        ]
        if missing:
            self.query_one(Static).update(
                self.heading
                + t(" · required: ", " · 必填：")
                + t(", ", "、").join(_label(name) for name in missing)
            )
            return
        self.dismiss({name: value for name, value in values.items() if value})

    def action_cancel(self) -> None:
        self.dismiss(None)


class CacheScreen(Modal[bool]):
    BINDINGS = [
        Binding("p", "plan", t("Build digest", "生成摘要")),
        Binding("escape", "dismiss_screen", t("Close", "关闭")),
    ]

    def __init__(self, deps: Deps) -> None:
        super().__init__()
        self.deps = deps
        self.entries: list[cache_service.Entry] = []
        self.digest = ""
        self.table: DataTable[Any] = DataTable()
        self.status = Static("")
        self.field = Input(placeholder=t("type purge to confirm", "输入 purge 确认"))

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(t("Reclaimable space", "可回收空间"), classes="panel-title"),
            self.table,
            self.status,
            self.field,
            Horizontal(
                Button(t("Build digest", "生成摘要"), id="cache-plan"),
                Button(t("Purge", "清理"), id="cache-purge", variant="error"),
                Button(t("Close", "关闭"), id="cache-close"),
                classes="dialog-actions",
            ),
            classes="panel",
            id="cache",
        )

    def on_mount(self) -> None:
        self.table.cursor_type = "row"
        self.table.add_columns(t("Kind", "类型"), t("Entry", "条目"), t("Bytes", "字节"))
        extra = (
            {} if self.deps.private_store is None else {"private_store": self.deps.private_store}
        )
        self.entries = self.deps.audit(self.deps.repositories, self.deps.store, **extra)
        for entry in self.entries:
            self.table.add_row(_sanitize(entry.kind), _sanitize(entry.identity), str(entry.bytes))
        self.status.update(
            t(
                f"{len(self.entries)} items, {sum(e.bytes for e in self.entries)} bytes in total",
                f"{len(self.entries)} 项，共 {sum(e.bytes for e in self.entries)} 字节",
            )
        )

    def action_plan(self) -> None:
        self.digest = cache_service.digest(self.entries)
        self.status.update(
            t(
                f"Digest {self.digest[:16]}…, type purge to confirm",
                f"摘要 {self.digest[:16]}…，输入 purge 确认",
            )
        )
        self.field.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "cache-plan":
            self.action_plan()
        elif event.button.id == "cache-purge":
            self._purge()
        elif event.button.id == "cache-close":
            self.action_dismiss_screen()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._purge()

    def _purge(self) -> None:
        if self.field.value != "purge" or not self.digest:
            self.status.update(t("Not confirmed", "未确认"))
            return
        extra = (
            {} if self.deps.private_store is None else {"private_store": self.deps.private_store}
        )
        result = self.deps.purge(self.deps.repositories, self.deps.store, self.digest, **extra)
        self.status.update(
            t(f"Deleted {result['deleted_bytes']} bytes", f"已删除 {result['deleted_bytes']} 字节")
        )
        self.dismiss(True)

    def action_dismiss_screen(self) -> None:
        self.dismiss(False)


class ParamEditScreen(Modal[list[dict[str, Any]] | None]):
    """Adjust parameters for the current transform pipeline."""

    BINDINGS = [
        Binding("ctrl+enter", "confirm", t("Confirm", "确认"), priority=True),
        Binding("m", "toggle_mode", t("Switch mode", "切换模式")),
        Binding("escape", "cancel", "取消"),
    ]

    def __init__(self, preset_name: str, pipeline: list[dict[str, Any]]) -> None:
        super().__init__()
        from imagen_cli.application.operator_catalog import (
            Parameter,
            get_operator_item,
            localized_operator,
        )

        self.preset_name = preset_name
        self.pipeline = copy.deepcopy(pipeline)
        self.mode: str = "basic"
        self.fields_info: list[tuple[str, str, str, str, int, str, bool]] = []
        self.inputs: list[Input | Select[Any]] = []
        self.initial_values: list[Any] = []
        self.parameters: dict[str, Parameter] = {}

        for step_idx, step in enumerate(self.pipeline):
            op = step.get("op", "")
            params = step.get("params", {})
            catalog_item = get_operator_item(op)
            if catalog_item is not None:
                catalog_item = localized_operator(catalog_item)
            catalog_params = {p.name: p for p in catalog_item.parameters} if catalog_item else {}

            all_param_names = list(params.keys())
            if catalog_item:
                for p in catalog_item.parameters:
                    if p.name not in params:
                        all_param_names.append(p.name)

            for k in all_param_names:
                p_meta = catalog_params.get(k)
                v = params.get(k, p_meta.default if p_meta else None)
                field_id = f"f_{step_idx}_{k}"
                if p_meta:
                    self.parameters[field_id] = p_meta
                is_adv = p_meta.is_advanced if p_meta else False

                if p_meta:
                    prefix = t("[advanced] ", "[高级] ") if is_adv else ""
                    label = f"{prefix}{p_meta.label} ({k})"
                    placeholder = t(
                        f"default {p_meta.default} · {p_meta.description}",
                        f"默认 {p_meta.default} · {p_meta.description}",
                    )
                    expected_type = p_meta.param_type
                    if expected_type == "choice" and type(p_meta.default) is int:
                        expected_type = "int"
                else:
                    label = f"{op}.{k}"
                    placeholder = t(f"current value: {v}", f"当前值: {v}")
                    expected_type = type(v).__name__

                self.fields_info.append(
                    (field_id, label, placeholder, expected_type, step_idx, k, is_adv)
                )
                if expected_type in ("bool", "boolean"):
                    options: list[Any] = [True, False]
                else:
                    options = list(p_meta.choices or []) if p_meta else []
                if options:
                    # Keep invalid existing values visible so confirmation can report them.
                    if not any(type(v) is type(o) and v == o for o in options):
                        options.append(v)
                    widget: Input | Select[Any] = Select(
                        [(str(option), option) for option in options],
                        value=v,
                        allow_blank=False,
                        id=field_id,
                    )
                    widget.value = v
                else:
                    widget = Input(
                        value=str(v) if v is not None else "",
                        placeholder=placeholder,
                        id=field_id,
                    )
                self.inputs.append(widget)
                self.initial_values.append(widget.value)

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel", id="param-form"):
            yield Static(
                t(f"Parameters · {self.preset_name}", f"参数调节 · {self.preset_name}"),
                classes="panel-title",
            )
            yield Static("", id="param-mode-indicator")
            for (field_id, label, _, _, _, _, _), inp in zip(
                self.fields_info, self.inputs, strict=True
            ):
                with Horizontal(classes="field", id=f"c_{field_id}"):
                    yield Static(label, classes="field-label")
                    yield inp
            yield Static("", id="param-error", markup=False)
            yield Horizontal(
                Button(t("Save", "保存"), id="param-save", variant="primary"),
                Button(t("Basic / advanced", "基础 / 高级"), id="param-mode"),
                Button(t("Cancel", "取消"), id="param-cancel"),
                classes="dialog-actions",
            )
            yield Static(
                t(
                    "ctrl+enter confirm · Tab next field · m basic/advanced · esc cancel",
                    "ctrl+enter 确认 · Tab 下一项 · m 基础/高级 · esc 取消",
                ),
                classes="panel-hint",
            )

    def on_mount(self) -> None:
        self._update_mode_view()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "param-save":
            self.action_confirm()
        elif event.button.id == "param-mode":
            self.action_toggle_mode()
        elif event.button.id == "param-cancel":
            self.action_cancel()

    def _update_mode_view(self) -> None:
        if self.mode == "basic":
            indicator_text = t(
                "Mode: [bold #e98cab]● Basic parameters[/]   [dim]○ All advanced parameters[/]"
                "  (m)",
                "模式: [bold #e98cab]● 基础浓缩参数[/]   [dim]○ 全部高级参数[/]  (m 切换)",
            )
        else:
            indicator_text = t(
                "Mode: [dim]○ Basic parameters[/]   [bold #e98cab]● All advanced parameters[/]"
                "  (m)",
                "模式: [dim]○ 基础浓缩参数[/]   [bold #e98cab]● 全部高级参数[/]  (m 切换)",
            )
        self.query_one("#param-mode-indicator", Static).update(indicator_text)

        first_visible: Input | Select[Any] | None = None
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
        for (field_id, label, _, type_str, step_idx, k, _), inp, initial in zip(
            self.fields_info, self.inputs, self.initial_values, strict=True
        ):
            try:
                if isinstance(inp, Select):
                    val: Any = inp.value
                    if type_str in ("bool", "boolean") and type(val) is not bool:
                        raise ValueError(t("choose true or false", "请选择 true 或 false"))
                else:
                    raw = inp.value
                    if type_str in ("int", "integer"):
                        val = int(raw)
                    elif type_str in ("float", "number"):
                        val = float(raw)
                        if not math.isfinite(val):
                            raise ValueError(t("must be finite", "必须是有限数值"))
                    else:
                        val = raw
                meta = self.parameters.get(field_id)
                if meta:
                    if meta.choices and not any(
                        type(val) is type(option) and val == option for option in meta.choices
                    ):
                        raise ValueError(t("choose a valid value", "请选择有效值"))
                    if meta.min_value is not None and val < meta.min_value:
                        raise ValueError(f">= {meta.min_value}")
                    if meta.max_value is not None and val > meta.max_value:
                        raise ValueError(f"<= {meta.max_value}")
                if inp.value != initial:
                    new_pipeline[step_idx].setdefault("params", {})[k] = val
            except (ValueError, TypeError) as exc:
                raise ValueError(f"{label}: {exc}") from exc
        return new_pipeline

    def action_confirm(self) -> None:
        try:
            new_pipeline = self._save_inputs_to_pipeline()
        except ValueError as exc:
            self.query_one("#param-error", Static).update(str(exc))
            return
        self.dismiss(new_pipeline)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_confirm()

    def action_cancel(self) -> None:
        self.dismiss(None)


class TransformScreen(Modal[bool]):
    AUTO_FOCUS = ""
    BINDINGS = [
        Binding("up", "cursor_up", t("Previous", "上一个"), show=False),
        Binding("down", "cursor_down", t("Next", "下一个"), show=False),
        Binding("c", "toggle_category", t("Switch category", "切换分类")),
        Binding("tab", "toggle_category", t("Switch category", "切换分类"), show=False),
        Binding("p", "edit_params", t("Adjust parameters", "参数调节")),
        Binding("enter", "apply", t("Apply", "应用")),
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

        self.entries = {entry["key"]: entry for entry in browse_entries(include_aliases=True)}
        self.categories = (
            [
                "All",
                "Combined recipes",
                "Smoothing and artifacts",
                "Lines and color areas",
                "Color and tone",
                "Style and optics",
                "Model restoration",
                "Size and geometry",
            ]
            if not is_chinese()
            else [
                "全部",
                "综合配方",
                "平滑与缺陷",
                "线条与色块",
                "颜色明暗",
                "风格光学",
                "模型恢复",
                "尺寸几何",
            ]
        )
        self.all_names = list(self.entries)
        self.filtered_names = list(self.all_names)
        self.names = self.filtered_names
        self.current_category_idx = 0
        self.pipelines: dict[str, list[dict[str, Any]]] = {
            k: [dict(step, params=dict(step.get("params", {}))) for step in v]
            for k, v in PRESETS.items()
        }
        # Local model restoration is reachable from the panel, but the model and its licence
        # are asked for on every run instead of being baked into a catalogue entry.
        from imagen_cli.application import operator_catalog

        item = operator_catalog.get_operator_item("superres")
        if item is not None:
            # Name and group are the CLI's own labels for this operator's category.
            label = operator_catalog.category_label(item.category)
            self.entries["superres"] = {"key": "superres", "name": label, "group": label}
            self.pipelines["superres"] = [{"op": "superres", "params": {}}]
            self.all_names.append("superres")
            self.filtered_names.append("superres")
        self.category_table: DataTable[Any] = DataTable(id="transform-categories")
        self.table: DataTable[Any] = DataTable(id="transform-presets")
        self.status = Static("")
        self.view = Static("")
        self.intro_view = Static("", id="transform-intro-view")
        self.params_view = Static("", id="transform-params-view")
        self.header_title = Static(
            t(
                "Transform presets and postprocessing studio (experimental)",
                "变换预设与后处理工作室（实验性 experimental）",
            ),
            classes="panel-title",
        )

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel", id="transform"):
            yield self.header_title
            with Horizontal(id="transform-content"):
                with Vertical(id="transform-cat-col"):
                    yield self.category_table
                with Vertical(id="transform-preset-col"):
                    yield self.table
                with Vertical(id="transform-preview-col"):
                    yield self.view
                    yield self.intro_view
                    yield self.params_view
                    yield self.status
            yield Horizontal(
                Button(t("Parameters", "参数"), id="transform-params"),
                Button(t("Apply", "应用"), id="transform-apply", variant="primary"),
                Button(t("Cancel", "取消"), id="transform-cancel"),
                classes="dialog-actions",
            )
            yield Static(
                t(
                    "↑↓ presets · c/Tab category · p parameters · enter apply · esc cancel",
                    "↑↓ 预设 · c/Tab 分类 · p 调参 · enter 应用 · esc 取消",
                ),
                classes="panel-hint",
            )

    def on_mount(self) -> None:
        self.category_table.cursor_type = "row"
        self.category_table.add_columns(t("Category", "分类"))
        for cat in self.categories:
            self.category_table.add_row(cat)
        self.category_table.can_focus = False
        self.category_table.move_cursor(row=0)

        self.table.cursor_type = "row"
        self.table.add_columns(t("Preset", "预设"))
        self._populate_table()
        self.table.can_focus = False
        self.table.move_cursor(row=0)
        self.set_focus(None)
        self.call_after_refresh(self.render_preview)

    def on_resize(self, event: events.Resize) -> None:
        self.set_class(event.size.width < 110, "compact")
        if not self.running:
            self.call_after_refresh(self.render_preview)

    def _populate_table(self) -> None:
        self.table.clear()
        for name in self.filtered_names:
            self.table.add_row(self.entries[name]["name"])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "transform-params":
            self.action_edit_params()
        elif event.button.id == "transform-apply":
            self.action_apply()
        elif event.button.id == "transform-cancel":
            self.action_cancel()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        event.stop()
        if self.running:
            return
        if event.data_table is self.category_table:
            index = self.category_table.cursor_row
            if index != self.current_category_idx:
                self._select_category(index)
        elif event.data_table is self.table:
            self.render_preview()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        if self.running:
            return
        if event.data_table is self.category_table:
            self._select_category(event.cursor_row)
        elif event.data_table is self.table:
            self.render_preview()

    def action_toggle_category(self) -> None:
        self._select_category((self.current_category_idx + 1) % len(self.categories))

    def _select_category(self, index: int) -> None:
        if self.running:
            return
        self.current_category_idx = index
        self.category_table.move_cursor(row=self.current_category_idx)
        cat_filter = self.categories[self.current_category_idx]
        if cat_filter in {"全部", "All"}:
            self.filtered_names = list(self.all_names)
            self.header_title.update(
                t(
                    "Transform presets and postprocessing studio (experimental)",
                    "变换预设与后处理工作室（实验性 experimental）",
                )
            )
        else:
            self.filtered_names = [
                n for n in self.all_names if self.entries[n]["group"] == cat_filter
            ]
            self.header_title.update(
                t(
                    f"Transform presets and postprocessing studio (experimental)"
                    f"  [dim #b0b7c2]({cat_filter})[/]",
                    f"变换预设与后处理工作室（实验性 experimental）"
                    f"  [dim #b0b7c2]({cat_filter})[/]",
                )
            )
        self.names = self.filtered_names
        self._populate_table()
        if self.names:
            self.table.move_cursor(row=0)
            self.render_preview()
        else:
            self.view.update("")
            self.intro_view.update(
                t("[dim]No presets in this category[/]", "[dim]该分类下无匹配预设[/]")
            )
            self.params_view.update("")

    def action_cursor_up(self) -> None:
        self._move(-1)

    def action_cursor_down(self) -> None:
        self._move(1)

    def _move(self, delta: int) -> None:
        if self.running or not self.names:
            return
        row = min(len(self.names) - 1, max(0, self.table.cursor_row + delta))
        self.table.move_cursor(row=row)
        self.render_preview()

    def _pipeline(self, name: str) -> list[dict[str, Any]]:
        """Edited parameters win; PRESETS is the catalogue default. Never index it eagerly."""
        return self.pipelines.get(name) or PRESETS[name]

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
        display = self.entries[name]["name"]
        pipeline = self._pipeline(name)
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
            preview_cols = max(1, min(96, self.view.content_size.width))
            preview_rows = max(1, min(28, self.view.content_size.height))
            self.view.update(
                Text.from_ansi(preview.ansi_image(thumbnail, cols=preview_cols, rows=preview_rows))
            )
            self.params_view.update(self._describe_pipeline(pipeline))
            self.status.update(
                t(f"{display} · p parameters · enter apply", f"{display} · p 调参 · enter 应用")
            )
        except (ValueError, KeyError, OSError, RuntimeError) as error:
            self.view.update(t("Preview unavailable", "预览不可用"))
            self.params_view.update(self._describe_pipeline(pipeline))
            self.status.update(_sanitize(error))

    def action_edit_params(self) -> None:
        if self.running:
            return
        row = self.table.cursor_row
        if not 0 <= row < len(self.names):
            return
        name = self.names[row]
        display = self.entries[name]["name"]
        pipeline = self._pipeline(name)
        from imagen_cli.application.operator_catalog import get_operator_item

        def _step_has_params(step: dict[str, Any]) -> bool:
            if step.get("params"):
                return True
            item = get_operator_item(step.get("op", ""))
            return bool(item.parameters) if item is not None else False

        has_params = any(_step_has_params(s) for s in pipeline)
        if not has_params:
            self.status.update(t(f"{name} has no configurable parameters", f"{name} 无可配置参数"))
            return

        def on_saved(updated_pipeline: list[dict[str, Any]] | None) -> None:
            if updated_pipeline is not None:
                self.pipelines[name] = updated_pipeline
                self.render_preview()
                self.status.update(
                    t(f"{name} parameters updated · enter apply", f"{name} 参数已更新 · enter 应用")
                )

        self.app.push_screen(ParamEditScreen(display, pipeline), on_saved)

    def action_apply(self) -> None:
        if self.running:
            return
        row = self.table.cursor_row
        if not 0 <= row < len(self.names):
            return
        name = self.names[row]
        pipeline = self._pipeline(name)
        if any(step.get("op") == "superres" for step in pipeline):
            self._ask_local_model(pipeline)
            return
        self._run_pipeline(pipeline)

    def _ask_local_model(self, pipeline: list[dict[str, Any]]) -> None:
        """A licence is answered per run: a stored acceptance would be consent by default."""
        current = str(pipeline[0].get("params", {}).get("model") or "")

        def chosen(model: str | None) -> None:
            if model is None or self.running:
                return
            self._run_pipeline([
                {**step, "params": {**step.get("params", {}), "model": model, "accept": True}}
                if step.get("op") == "superres"
                else step
                for step in pipeline
            ])

        fetch = (
            (lambda model: _executor(self.deps)(
                "superres fetch", {"model": model, "accept": True}
            ))
            if self.deps.execute is not None
            else None
        )
        self.app.push_screen(LocalModelScreen(current, fetch=fetch), chosen)

    def _run_pipeline(self, pipeline: list[dict[str, Any]]) -> None:
        assert self.deps.transform is not None
        if self.deps.transform == self.deps._transform:
            self.running = True
            self.cancelled.clear()
            self.status.update(
                t(
                    "Processing · Esc cancels; a new artifact is saved on success",
                    "处理中 · Esc 取消，成功后保存新产物",
                )
            )
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
                execute,
                self.path,
                steps,
                cancelled=self.cancelled,
            )
            if self.cancelled.is_set():
                self.status.update(
                    t("Processing canceled; no artifact saved", "处理已取消，未保存产物")
                )
                return
            result = postprocess_service.publish_result(
                self.deps.repositories,
                self.deps.store,
                self.artifact,
                steps,
                data,
                metadata,
                session_id=self.session,
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
            self.status.update(t("Canceling processing…", "正在取消处理…"))
            return
        self.dismiss(False)


class ModelPickerScreen(Modal[str | None]):
    """Pick the default model from the declared catalog, with a text filter."""

    BINDINGS = [Binding("escape", "cancel", t("Close", "关闭"))]

    def __init__(self, models: list[dict[str, Any]], current: str) -> None:
        super().__init__()
        self.models = sorted(models, key=lambda row: str(row["model_id"]))
        self.current = current
        self.filter: Input = Input(
            placeholder=t("filter model id", "过滤 model id"), id="model-filter"
        )
        self.table: DataTable[Any] = DataTable(id="model-table")

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(t("Choose the default model", "选择默认模型"), classes="panel-title"),
            self.filter,
            self.table,
            Static(
                t(
                    "Type to filter · enter selects · esc cancels",
                    "输入过滤 · enter 选定 · esc 取消",
                ),
                classes="panel-hint",
            ),
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


def _weights_cells(row: dict[str, Any]) -> tuple[str, str, str, str]:
    """Model, scale, licence, state: the columns both local-weight panels show."""
    megabytes = int(row["bytes"] or 0) / 1048576
    state = (
        t(f"Downloaded {megabytes:.0f} MB", f"已下载 {megabytes:.0f} MB")
        if row["downloaded"]
        else t("Not downloaded", "未下载")
    )
    return (
        _sanitize(row["model"]),
        f"{row['scale']}x",
        _ellipsize(_sanitize(row["license"]), 16),
        state,
    )


class SuperresScreen(Modal[bool]):
    """Local upscaling models: the CLI's own catalog, and no download without consent."""

    BINDINGS = [
        Binding("f", "fetch", t("Download", "下载")),
        Binding("i", "install", t("Install runtime", "安装运行时")),
        Binding("escape", "dismiss_screen", t("Close", "关闭")),
    ]

    def __init__(self, deps: Deps, run: Callable[[str, dict[str, Any]], None]) -> None:
        super().__init__()
        self.deps = deps
        self.run = run
        self.rows: list[dict[str, Any]] = []
        self.table: DataTable[Any] = DataTable(id="superres-table")
        self.status = Static("")

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(t("Local models", "本地模型"), classes="panel-title"),
            Static(
                t(
                    "Weights stay on this machine and are never fetched on their own. "
                    "f downloads the selected model, i installs the inference runtime.",
                    "权重只保存在本机，不会自行下载。f 下载选中模型，i 安装推理运行时。",
                ),
                classes="panel-hint",
            ),
            self.table,
            self.status,
            Horizontal(
                Button(t("Download", "下载"), id="superres-fetch", variant="primary"),
                Button(t("Install runtime", "安装运行时"), id="superres-install"),
                Button(t("Close", "关闭"), id="superres-close"),
                classes="dialog-actions",
            ),
            classes="panel",
            id="superres",
        )

    def on_mount(self) -> None:
        self.table.cursor_type = "row"
        # A long licence would push the state column off an 80-column panel; the consent
        # dialog carries the full text.
        self.table.add_columns(
            t("Model", "模型"), t("Scale", "倍率"), t("License", "许可"), t("State", "状态")
        )
        self.refresh_rows()

    def on_screen_resume(self) -> None:
        self.refresh_rows()  # a finished download shows up once the result panel closes

    def refresh_rows(self) -> None:
        report = superres.catalog()
        self.rows = list(report["models"])
        self.table.clear()
        for row in self.rows:
            self.table.add_row(*_weights_cells(row), key=_sanitize(row["model"]))
        runtime = t("runtime ready", "运行时可用") if report["onnxruntime"] else t(
            "runtime missing", "缺少运行时"
        )
        self.status.update(
            f"{runtime} · {_sanitize(report['model_dir'])}" if self.rows else runtime
        )

    def _selected(self) -> dict[str, Any] | None:
        if not self.rows:
            return None
        return self.rows[self.table.cursor_coordinate.row]

    def action_fetch(self) -> None:
        row = self._selected()
        if row is None:
            return
        name = str(row["model"])
        if not row["downloadable"]:
            self.status.update(
                t(f"{name} has no published download", f"{name} 没有可下载的来源")
            )
            return

        def done(confirmed: bool | None) -> None:
            if confirmed:
                self.run("superres fetch", {"model": name, "accept": True})

        self.app.push_screen(
            ConfirmScreen(
                t(
                    f"Download {name} from {row['source']}? License: {row['license']}",
                    f"从 {row['source']} 下载 {name}？许可：{row['license']}",
                )
            ),
            done,
        )

    def action_install(self) -> None:
        def done(confirmed: bool | None) -> None:
            if confirmed:
                self.run("superres install", {"accept": True})

        self.app.push_screen(
            ConfirmScreen(
                t(
                    "Install the inference runtime? This downloads packages from PyPI.",
                    "安装推理运行时？这会从 PyPI 下载安装包。",
                )
            ),
            done,
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        actions = {
            "superres-fetch": self.action_fetch,
            "superres-install": self.action_install,
            "superres-close": self.action_dismiss_screen,
        }
        action = actions.get(str(event.button.id))
        if action is not None:
            action()

    def action_dismiss_screen(self) -> None:
        self.dismiss(False)


class LocalModelScreen(Modal[str | None]):
    """Pick the local model for one transform run; nothing runs before this is answered.

    Weights that are not on disk yet are asked about, never fetched behind the user's back.
    """

    BINDINGS = [Binding("escape", "cancel", t("Cancel", "取消"))]

    def __init__(
        self,
        current: str = "",
        *,
        fetch: Callable[[str], tuple[str, dict[str, Any]]] | None = None,
    ) -> None:
        super().__init__()
        self.current = current
        self.fetch = fetch
        self.downloading = False
        self.rows: list[dict[str, Any]] = []
        self.table: DataTable[Any] = DataTable(id="local-model-table")
        self.status = Static("", id="local-model-status")

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(t("Choose the local model", "选择本地模型"), classes="panel-title"),
            Static(
                t(
                    "Weights run on this machine; licence and source are below. A model that "
                    "is not on disk is downloaded only after you confirm it.",
                    "权重在本机推理；许可与来源见下表。尚未下载的模型会先问你，确认后才下载。",
                ),
                classes="panel-hint",
            ),
            self.table,
            self.status,
            Horizontal(
                Button(t("Apply", "应用"), id="local-model-apply", variant="primary"),
                Button(t("Cancel", "取消"), id="local-model-cancel"),
                classes="dialog-actions",
            ),
            classes="panel",
            id="local-model",
        )

    def on_mount(self) -> None:
        self.table.cursor_type = "row"
        self.table.add_columns(
            t("Model", "模型"), t("Scale", "倍率"), t("License", "许可"), t("State", "状态")
        )
        self._populate(self.current)

    def on_data_table_row_highlighted(self, _: DataTable.RowHighlighted) -> None:
        self._describe()

    def _populate(self, select: str = "") -> None:
        self.rows = list(superres.catalog()["models"])
        self.table.clear()
        for row in self.rows:
            self.table.add_row(*_weights_cells(row), key=_sanitize(row["model"]))
        names = [str(row["model"]) for row in self.rows]
        if names:
            self.table.move_cursor(row=names.index(select) if select in names else 0)
            self._describe()

    def _current(self) -> dict[str, Any] | None:
        if not self.rows or self.table.row_count == 0:
            return None
        return self.rows[self.table.cursor_coordinate.row]

    def _describe(self) -> None:
        row = self._current()
        if row is None:
            return
        tail = (
            t("enter runs it", "enter 应用")
            if row["downloaded"]
            else t("enter asks to download it first", "enter 会先问是否下载")
        )
        self.status.update(
            f"{_sanitize(row['model'])} · {_weights_cells(row)[3]} · {tail}"
        )

    def _choose(self) -> None:
        row = self._current()
        if row is None or self.downloading:
            return
        model = str(row["model"])
        if row["downloaded"]:
            self.dismiss(model)
            return
        if self.fetch is None:
            self.status.update(t(f"{model} is not on disk yet", f"{model} 尚未下载"))
            return

        def confirmed(answer: bool | None) -> None:
            if answer:
                self._start_download(model)

        self.app.push_screen(
            ConfirmScreen(
                t(
                    f"Download {model} from {row['source']}? License: {row['license']}",
                    f"从 {row['source']} 下载 {model}？许可：{row['license']}",
                )
            ),
            confirmed,
        )

    def _start_download(self, model: str) -> None:
        self.downloading = True
        self.status.update(t(f"Downloading {model}…", f"正在下载 {model}…"))
        self._download(model)

    @work(thread=True, group="local-model-download")
    def _download(self, model: str) -> None:
        try:
            status, _ = self.fetch(model) if self.fetch else ("failed", {})
            message = t(f"{model}: {status} · enter runs it", f"{model}：{status} · enter 应用")
        except Exception as error:  # noqa: BLE001 - reported in the panel
            message = _sanitize(error)
        self.app.call_from_thread(self._downloaded, message)

    def _downloaded(self, message: str) -> None:
        if not self.is_mounted:
            return
        self.downloading = False
        row = self._current()
        self._populate(str(row["model"]) if row else "")
        self.status.update(message)

    def on_data_table_row_selected(self, _: DataTable.RowSelected) -> None:
        self._choose()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "local-model-apply":
            self._choose()
        elif event.button.id == "local-model-cancel":
            self.action_cancel()

    def action_cancel(self) -> None:
        self.dismiss(None)


class ProvidersScreen(Modal[bool]):
    BINDINGS = [
        Binding("a", "add", t("Add", "新增")),
        Binding("u", "update", t("Update", "更新")),
        Binding("m", "refresh_models", t("Refresh models", "刷新模型")),
        Binding("n", "add_model", t("Add model", "添加模型")),
        Binding("escape", "dismiss_screen", t("Close", "关闭")),
    ]
    FIELDS = (
        ("profile", "profile id", ""),
        ("provider", "openai / gemini", "openai"),
        ("family", "images / generate_content", "images"),
        ("base_url", "https://…", ""),
        ("models_path", "/v1/models", "/v1/models"),
        ("model_default", "model id", ""),
        (
            "credential_ref",
            t(
                "env:NAME / keyring:SERVICE / private:NAME (may be empty)",
                "env:NAME / keyring:SERVICE / private:NAME（可留空）",
            ),
            "env:IMAGEN_KEY",
        ),
        (
            "api_key",
            t(
                "Enter an API key directly (stored privately, never echoed)",
                "直接填 API key（写入私有存储，不回显）",
            ),
            "",
        ),
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
                t(
                    "a add · u update · m refresh models · n enter model · enter selects "
                    "the default model\n"
                    "Add or update can take an API key directly, stored in workspace private "
                    "storage; credential_ref may stay empty",
                    "a 新增 · u 更新 · m 刷新模型 · n 手输模型 · enter 选择默认模型\n"
                    "新增/更新时可直接填 API key，写入 workspace 私有存储；credential_ref 可留空",
                ),
                classes="panel-hint",
            ),
            self.table,
            self.status,
            Horizontal(
                Button(t("Add", "新增"), id="providers-add", variant="primary"),
                Button(t("Update", "更新"), id="providers-update"),
                Button(t("Models", "刷新模型"), id="providers-models"),
                Button(t("Enter model", "手输模型"), id="providers-manual"),
                Button(t("Close", "关闭"), id="providers-close"),
                classes="dialog-actions",
            ),
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
            raise ValueError(
                t(
                    "Enter an API key or provide an env:/keyring:/private: reference",
                    "填写 API key，或提供 env:/keyring:/private: 引用",
                )
            )
        return parameters

    def _fetch_draft_models(self, values: dict[str, str]) -> list[str]:
        from imagen_cli.application.model_catalog_service import fetch
        from imagen_cli.execution.configured import resolve_credential

        def credential(reference: str) -> str:
            return values.get("api_key") or resolve_credential(
                reference, private_store=self.deps.private_store,
            )

        catalog = fetch(
            {**values, "credential_ref": values.get("credential_ref") or "private:draft"},
            send_credential=True, resolve_credential=credential,
        )
        return [str(row["model_id"]) for row in catalog["models"]]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        actions = {
            "providers-add": self.action_add,
            "providers-update": self.action_update,
            "providers-models": self.action_refresh_models,
            "providers-manual": self.action_add_model,
            "providers-close": self.action_dismiss_screen,
        }
        action = actions.get(str(event.button.id))
        if action is not None:
            action()

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
            ProviderForm(
                t("Add profile", "新增 profile"),
                [(name, hint, "" if name == "credential_ref" else value)
                 for name, hint, value in self.FIELDS],
                fetch_models=self._fetch_draft_models,
            ),
            done,
        )

    def action_update(self) -> None:
        profile = self._selected()
        if profile is None:
            self.status.update(t("No profile selected", "没有选中 profile"))
            return
        from urllib.parse import urlsplit

        profile = dict(profile)
        if profile.get("models_path") is None:
            profile["models_path"] = urlsplit(str(profile["base_url"])).path.rstrip("/") + "/models"
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
            ProviderForm(
                t("Update profile", "更新 profile"),
                defaults,
                fetch_models=self._fetch_draft_models,
            ),
            done,
        )

    def action_refresh_models(self) -> None:
        profile = self._selected()
        if profile is None or self.deps.models_refresh is None:
            self.status.update(t("No profile selected", "没有选中 profile"))
            return
        try:
            status, data = self.deps.models_refresh(str(profile["id"]))
        except (ValueError, OSError) as error:
            self.status.update(_sanitize(error))
            return
        self.status.update(
            t(
                f"{status}: {len(data.get('models', []))} models",
                f"{status}: {len(data.get('models', []))} 个模型",
            )
        )

    def action_add_model(self) -> None:
        profile = self._selected()
        if profile is None or self.deps.models_add is None:
            self.status.update(t("No profile selected", "没有选中 profile"))
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
            FormScreen(t("Add model", "添加模型"), [("model", "model id", "")]), done
        )

    def on_data_table_row_selected(self, _: DataTable.RowSelected) -> None:
        self.action_choose_model()

    def action_choose_model(self) -> None:
        profile = self._selected()
        if profile is None:
            self.status.update(t("No profile selected", "没有选中 profile"))
            return
        rows = queries.iter_all(
            self.deps.repositories, "provider_models", profile_id=str(profile["id"])
        )
        if not rows:
            self.status.update(
                t(
                    "No models declared; press m to refresh or n to enter a model id",
                    "尚未声明模型；按 m 刷新，或按 n 手输 model id",
                )
            )
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


class PlanScreen(Modal[bool]):
    """Show a frozen plan; approval and execution are separate keys."""

    AUTO_FOCUS = ""

    BINDINGS = [
        Binding("a", "approve", t("Approve", "批准")),
        Binding("enter", "execute", t("Execute", "执行")),
        Binding("r", "retry", t("Retry", "重试")),
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
            Static(t("Execution plan", "执行计划"), classes="panel-title"),
            Static(
                t(
                    f"Plan {self.plan.get('id')}\n"
                    f"Operation {self.plan.get('operation')} · Model {self.plan.get('model')}",
                    f"计划 {self.plan.get('id')}\n"
                    f"操作 {self.plan.get('operation')} · 模型 {self.plan.get('model')}",
                )
            ),
            Static(
                t(f"Digest {self.plan.get('plan_digest')}", f"摘要 {self.plan.get('plan_digest')}"),
                classes="panel-hint",
            ),
            self.status,
            Horizontal(
                Button(t("Approve", "批准"), id="plan-approve"),
                Button(t("Execute", "执行"), id="plan-execute", variant="primary", disabled=True),
                Button(t("Retry", "重试"), id="plan-retry"),
                Button(t("Cancel", "取消"), id="plan-cancel"),
                classes="dialog-actions",
            ),
            Static(
                t(
                    "a approve · enter execute · r retry · esc cancel",
                    "a 批准 · enter 执行 · r 重试 · esc 取消",
                ),
                classes="panel-hint",
            ),
            classes="panel",
            id="plan",
        )

    def on_mount(self) -> None:
        self._say(t("Not approved yet", "尚未批准"), "muted")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        actions = {
            "plan-approve": self.action_approve,
            "plan-execute": self.action_execute,
            "plan-retry": self.action_retry,
            "plan-cancel": self.action_cancel,
        }
        action = actions.get(str(event.button.id))
        if action is not None:
            action()

    def _say(self, message: str, tone: str = "muted") -> None:
        self.status.update(Text(message, style=_TONES[tone]))
        self.query_one("#plan-execute", Button).disabled = not self.approved or self.running
        for identity in ("approve", "retry", "cancel"):
            self.query_one(f"#plan-{identity}", Button).disabled = self.running

    def _approval_state(self) -> str | None:
        rows = self.deps.repositories.list("approvals", plan_id=self.plan["id"])
        return str(rows[0]["state"]) if rows else None

    def action_approve(self) -> None:
        if self.running:
            return
        if self.deps.execute is None:
            self._say(t("Executor unavailable", "执行器不可用"), "error")
            return
        state = self._approval_state()
        if state == "consumed":
            # The plan already dispatched once; Enter shows the recorded outcome.
            self.approved = True
            self._say(
                t(
                    "This plan already ran · enter shows the result",
                    "该计划已执行 · enter 查看结果",
                ),
                "info",
            )
            return
        if state == "expired":
            self._say(
                t(
                    "This plan expired · close with esc and create a new one",
                    "该计划已过期 · esc 关闭后重新生成",
                ),
                "error",
            )
            return
        try:
            status, _ = self.deps.execute(
                "plan approve",
                {"plan_id": self.plan["id"], "digest": self.plan["plan_digest"]},
            )
        except (ValueError, OSError) as error:
            self._say(t("Approval failed: ", "批准失败：") + _sanitize(error), "error")
            return
        if status in {"approved", "succeeded"}:
            self.approved = True
            self._say(t("Approved · enter executes", "已批准 · enter 执行"), "ok")
        else:
            self._say(t(f"Approval failed: {status}", f"批准失败：{status}"), "error")

    def action_execute(self) -> None:
        if self.running:
            return
        if not self.approved:
            self._say(t("Press a to approve first", "先按 a 批准"), "info")
            return
        if self.deps.execute is None:
            self._say(t("Executor unavailable", "执行器不可用"), "error")
            return
        self.running = True
        self._say(
            t(
                "Executing… the first request may take 20–60 seconds; returns automatically",
                "执行中… 首次请求可能 20–60 秒，完成后自动返回",
            ),
            "info",
        )
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
            self._say(t("Execution failed: ", "执行失败：") + error, "error")
            return
        assert result is not None
        status, data = result
        job = data.get("job") or {}
        if status in {"succeeded", "accepted", "partial_success"}:
            self._say(
                t(
                    f"Execution finished: {job.get('state')} · {job.get('id')}",
                    f"执行完成：{job.get('state')} · {job.get('id')}",
                ),
                "ok",
            )
            self.dismiss(True)
        else:
            reason = error_message(job.get("error") or data.get("error"))
            identity = job.get("id")
            self._say(
                t(f"Execution result: {status}", f"执行结果：{status}")
                + (f" · {reason}" if reason else "")
                + (f" · {identity}" if identity else ""),
                "error",
            )

    def action_retry(self) -> None:
        if self.running:
            return
        if self.deps.execute is None:
            self._say(t("Executor unavailable", "执行器不可用"), "error")
            return
        try:
            status, data = self.deps.execute("plan retry", {"plan_id": self.plan["id"]})
        except (ValueError, OSError) as error:
            self._say(t("Retry failed: ", "重试失败：") + _sanitize(error), "error")
            return
        plan = data.get("plan") or {}
        if plan.get("id"):
            self.app.push_screen(PlanScreen(self.deps, plan))
        else:
            self._say(t(f"Retry result: {status}", f"重试结果：{status}"), "error")

    def action_cancel(self) -> None:
        if self.running:
            self._say(t("Executing, please wait…", "执行中，请稍候…"), "info")
            return
        self.dismiss(False)


def _boolean(raw: str) -> bool:
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(t("Choose true or false", "请选择 true 或 false"))


def _convert_arguments(command: str, values: dict[str, str]) -> dict[str, Any]:
    schema = discover(command)["input_schema"]
    properties = schema.get("properties") or {}
    arguments: dict[str, Any] = {}
    for name, raw in values.items():
        spec = properties.get(name) or {}
        kind = spec.get("type")
        if kind is None and "anyOf" in spec:
            kind = next((item.get("type") for item in spec["anyOf"] if item.get("type")), None)
        if kind is None and isinstance(spec.get("const"), bool):
            # `--yes` is declared as const true; without this it stays a string.
            kind = "boolean"
        if not raw:
            continue
        if kind == "integer":
            arguments[name] = int(raw)
        elif kind == "boolean":
            arguments[name] = _boolean(raw)
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


class ResultScreen(Modal[None]):
    BINDINGS = [Binding("escape", "close", t("Close", "关闭"))]

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
            Button(t("Close", "关闭"), classes="dialog-close"),
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

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_close()

    def action_close(self) -> None:
        self.dismiss(None)


class CommandPaletteScreen(Modal[str | None]):
    """Every registered CLI command; the form is generated from its schema."""

    BINDINGS = [
        Binding("up", "cursor_up", t("Previous", "上一个"), show=False),
        Binding("down", "cursor_down", t("Next", "下一个"), show=False),
        Binding("enter", "select", t("Open", "打开")),
        Binding("escape", "close", t("Close", "关闭")),
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
        self.filter: Input = Input(
            placeholder=t("filter commands or descriptions", "过滤命令或说明"), id="palette-filter"
        )
        self.status = Static("")

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(t("All commands", "全部命令"), classes="panel-title"),
            self.filter,
            self.table,
            self.status,
            Static(
                t(
                    "Type to filter · ↑↓ select · enter opens · esc closes",
                    "输入过滤 · ↑↓ 选择 · enter 打开 · esc 关闭",
                ),
                classes="panel-hint",
            ),
            classes="panel",
            id="palette",
        )

    def on_mount(self) -> None:
        self.table.cursor_type = "row"
        self.table.add_columns(t("Command", "命令"), t("Description", "说明"))
        self.table.can_focus = False
        self._populate()
        self.filter.focus()

    def _populate(self) -> None:
        needle = self.filter.value.strip().lower()
        self.matches = [
            name
            for name in self.names
            if (not needle or needle in name.lower() or needle in command_summary(name).lower())
        ]
        self.table.clear()
        for name in self.matches:
            self.table.add_row(_sanitize(name), _sanitize(command_summary(name)))
        if self.matches:
            self.table.move_cursor(row=0)
        self.status.update(t(f"{len(self.matches)} commands", f"{len(self.matches)} 个命令"))

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


# Shared options the TUI can pass as run options, keyed by form field name.
_CONTROL_ATTRS = {
    "async": "async_",
    "dry_run": "dry_run",
    "wait": "wait",
    "timeout": "timeout",
}


# Commands whose `profile` field must name a profile that already exists.
_EXISTING_PROFILE_COMMANDS = {
    "providers", "models list", "models refresh", "models add", "models use",
    "profile show", "profile update", "profile set", "profile secret",
    "session create", "generate", "edit",
}


def _control_fields(command: str) -> list[tuple[str, str, str]]:
    """Run options this command accepts as shared options, not arguments."""
    allowed = COMMAND_CONTROLS.get(command, set())
    taken = {field[0] for field in command_fields(command)[0]}
    fields = []
    for name, attribute in _CONTROL_ATTRS.items():
        if attribute not in allowed:
            continue
        # `asset gc` reads dry_run as an argument; the schema field already asks for it.
        if name == "dry_run" and name in taken:
            continue
        fields.append((name, "seconds" if name == "timeout" else "true | false", ""))
    return fields


def _convert_controls(values: dict[str, str]) -> dict[str, Any]:
    converted: dict[str, Any] = {}
    for name, attribute in _CONTROL_ATTRS.items():
        raw = values.get(name, "").strip()
        if not raw:
            continue
        if name == "timeout":
            converted[attribute] = int(raw)
        else:
            converted[attribute] = _boolean(raw)
    return converted


def _control_options(
    base: SharedOptions | None, overrides: dict[str, Any]
) -> SharedOptions | None:
    if not overrides:
        return base
    values = (base or SharedOptions()).model_dump()
    values.update(overrides)
    # Rebuild rather than model_copy: the CLI's shared-option rules must apply.
    return SharedOptions(**values)


def _creation_arguments(values: dict[str, str], operation: str, session: str) -> dict[str, Any]:
    """Creation arguments from a form; empty geometry fields stay unsent."""
    arguments: dict[str, Any] = {
        "schema_version": 1,
        "operation": operation,
        "session": session,
        "prompt": values["prompt"],
        "count": int(values.get("count", "1")),
    }
    for name in ("size", "aspect_ratio", "image_size"):
        if values.get(name):
            arguments[name] = values[name]
    if values.get("profile"):
        arguments["profile"] = values["profile"]
    if values.get("model"):
        arguments["model"] = values["model"]
    return arguments


def _needs_form(command: str) -> bool:
    """Any field at all means the user may want to fill it, even if none is required.

    Paging-only commands keep running straight away; their fields are noise."""
    fields = [field for field in command_fields(command)[0] if field[0] not in {"limit", "cursor"}]
    return bool(fields or _control_fields(command))


# Runtime lookup so the help panel follows IMAGEN_LANG even when the module was
# imported before the fixture or command selected a language.
_BINDING_TEXT = {
    "quit": ("Quit", "退出"),
    "cache": ("Cache", "缓存"),
    "providers": ("Providers", "端点"),
    "superres": ("Local models", "本地模型"),
    "new_session": ("New session", "新建会话"),
    "generate": ("Generate", "生成"),
    "edit": ("Edit", "编辑"),
    "delete": ("Delete session", "删除会话"),
    "delete_artifact": ("Delete image", "删除图片"),
    "fork": ("Fork session", "分支会话"),
    "export": ("Export session", "导出会话"),
    "transform": ("Transform", "变换"),
    "open_image": ("Open image", "打开图片"),
    "open_folder": ("Open folder", "打开目录"),
    "previous_artifact": ("Previous artifact", "上一张产物"),
    "next_artifact": ("Next artifact", "下一张产物"),
    "refresh": ("Refresh", "刷新"),
    "commands": ("All commands", "全部命令"),
    "help": ("Help", "帮助"),
    "toggle_language": ("Switch language", "切换语言"),
}


def _help_lines(rows: list[tuple[str, str]]) -> list[str]:
    """Two legend columns once the list outgrows the panel, so no key scrolls out of sight."""
    if len(rows) <= 10:
        return [f"{key}  {description}" for key, description in rows]
    half = (len(rows) + 1) // 2
    left, right = rows[:half], rows[half:]
    key_width = max(cell_len(key) for key, _ in rows)

    def entry(row: tuple[str, str]) -> str:
        key, description = row
        return f"{key:<{key_width}}  {description}"

    gutter = max(cell_len(entry(row)) for row in left) + 3
    lines = []
    for index, row in enumerate(left):
        line = entry(row)
        if index < len(right):
            line += " " * (gutter - cell_len(line)) + entry(right[index])
        lines.append(line.rstrip())
    return lines


class HelpScreen(Modal[None]):
    """Every shown binding in one panel; keeps the key bar short."""

    BINDINGS = [Binding("escape", "close", t("Close", "关闭"))]

    def __init__(self, rows: list[tuple[str, str]]) -> None:
        super().__init__()
        self.rows = rows

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(t("Keyboard shortcuts", "快捷键"), classes="panel-title"),
            *(Static(line) for line in _help_lines(self.rows)),
            Static(
                t(": opens all commands · esc closes", ": 打开全部命令 · esc 关闭"),
                classes="panel-hint",
            ),
            Button(t("Close", "关闭"), classes="dialog-close"),
            classes="panel",
            id="help",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_close()

    def action_close(self) -> None:
        self.dismiss(None)


class WorkbenchApp(App[None]):
    CSS = """
    $canvas: #090b10;
    $surface: #11151d;
    $surface-raised: #181e28;
    $surface-active: #222a36;
    $line: #303948;
    $ink: #f4f1ef;
    $ink-soft: #b0b7c2;
    $ink-muted: #7f8998;
    $accent: #e98cab;
    $accent-ink: #24131b;
    $success: #81c995;
    $warning: #f2c879;
    $danger: #ed8b8b;

    Screen { background: $canvas; color: $ink; }
    * {
        scrollbar-color: $line; scrollbar-background: $surface;
        scrollbar-color-hover: $accent; scrollbar-color-active: $accent;
    }
    #topbar { height: 2; background: $surface; padding: 0 2; border-bottom: solid $line; }
    #brand { width: auto; color: $accent; text-style: bold; content-align: left middle; }
    #workspace { width: 1fr; color: $ink-muted; content-align: right middle; }
    #body { height: 1fr; }
    #sidebar {
        width: 32; min-width: 24; background: $surface;
        border-right: solid $line; padding: 1 1 0 1;
    }
    #sessions { height: 1fr; background: $surface; }
    DataTable {
        background: transparent; color: $ink-soft;
        scrollbar-size-vertical: 1; scrollbar-size-horizontal: 1;
    }
    DataTable > .datatable--header {
        background: $surface-raised; color: $ink-muted; text-style: bold;
    }
    DataTable > .datatable--cursor { background: $accent; color: $accent-ink; text-style: bold; }
    DataTable > .datatable--hover { background: $surface-active; }
    #detail-pane { width: 1fr; padding: 1 2 0 2; }
    #detail { color: $ink; text-style: bold; height: 1; overflow: hidden; }
    #meta { color: $ink-muted; height: 1; overflow: hidden; }
    #preview-frame {
        height: 1fr; margin-top: 1; background: $canvas;
        border: solid $line; align: center middle; padding: 1;
    }
    #preview { width: auto; height: auto; color: $ink-soft; text-align: center; }
    #film { height: 1; overflow: hidden; color: $ink-soft; }
    # Controls stay one line tall: terminal rows belong to images, not chrome.
    # shortcut: single-line controls carry no border, so focus shows as fill
    # rather than an outline; revisit if a theme needs bordered buttons back.
    #action-bar { height: 1; margin-top: 1; }
    #action-gap { width: 1fr; height: 1; }
    #action-bar Button {
        height: 1; width: auto; min-width: 6; margin-right: 1; padding: 0 1;
        background: $surface-raised; color: $ink-soft; border: none;
    }
    #action-bar Button:hover { background: $surface-active; color: $ink; }
    #action-bar Button:focus { background: $accent; color: $accent-ink; text-style: bold; }
    #action-bar Button.-primary { background: $accent; color: $accent-ink; }
    #action-bar Button.-primary:focus { text-style: bold underline; }
    #action-bar Button:disabled { background: $surface; color: $ink-muted; }
    #action-bar #action-prev, #action-bar #action-next {
        width: 3; min-width: 3; margin-right: 0; padding: 0;
    }
    #status { height: 1; overflow: hidden; color: $ink-muted; }
    #keybar { height: 2; background: $surface; padding: 0 1; border-top: solid $line; }
    ModalScreen { align: center middle; background: $canvas 85%; }
    .panel {
        background: $surface; border: solid $line; padding: 1 2;
        width: 80%; max-width: 96; height: auto; max-height: 94%; overflow-y: auto;
    }
    .panel-title { color: $ink; text-style: bold; padding-bottom: 1; }
    .panel-hint { color: $ink-muted; }
    .field { height: auto; }
    .field-label { width: 16; color: $ink-soft; content-align: left middle; }
    Input, Select { background: $surface-raised; color: $ink; }
    Input.-invalid, Select.-invalid { background: $danger; color: $canvas; }
    #providers, #palette, #model-picker, #superres, #local-model {
        height: 90%; overflow: hidden;
    }
    #providers DataTable, #palette DataTable, #model-picker DataTable,
    #superres DataTable, #local-model DataTable { height: 1fr; width: 1fr; }
    #transform { width: 92%; max-width: 200; height: 90%; overflow: hidden; }
    #transform-cat-col { width: 18%; min-width: 16; height: 1fr; border-right: solid $line; }
    #transform-preset-col { width: 33%; min-width: 26; height: 1fr; margin-left: 1; }
    #transform-categories, #transform-presets { height: 1fr; width: 1fr; }
    #transform-preview-col { width: 1fr; margin-left: 2; height: 1fr; }
    #transform-intro-view {
        height: auto; max-height: 5; overflow-y: auto; color: $ink-soft;
        margin: 1 0; padding: 0 1; background: $canvas; border-left: solid $accent;
    }
    #transform-params-view { height: 4; overflow-y: auto; color: $ink-muted; margin-top: 1; }
    #param-form { max-width: 82; max-height: 88%; overflow-y: auto; }
    #param-form .field-label { width: 24; }
    #param-form .panel-hint { margin-top: 1; }
    #palette-filter { margin-bottom: 1; width: 1fr; }
    ChoiceForm #choice-form { background: $surface; border: solid $line; }
    #form-status { color: $ink; text-style: bold; }
    SelectCurrent, Select > SelectCurrent {
        background: transparent; color: $ink; border: none; height: 1; padding: 0 1;
    }
    SelectOverlay { background: $surface-raised; border: solid $line; color: $ink-soft; }
    SelectOverlay > .option-list--option-highlighted {
        background: $accent; color: $accent-ink; text-style: bold;
    }
    # A checklist is the multi-value twin of Select: one row per option, same palette.
    SelectionList {
        height: auto; max-height: 12; background: $surface-raised; color: $ink-soft;
        border: none; padding: 0;
        & > .option-list--option-highlighted {
            background: $accent; color: $accent-ink; text-style: bold;
        }
        # Textual draws the same glyph in both states and only recolours it, so the box is
        # filled when ticked and invisible when not; the [x]/[ ] in the label is the words.
        & > .selection-list--button {
            color: $surface-raised; background: $surface-raised;
        }
        & > .selection-list--button-selected {
            color: $accent; background: $surface-raised;
        }
        & > .selection-list--button-highlighted,
        & > .selection-list--button-selected-highlighted {
            color: $accent-ink; background: $accent;
        }
    }
    Button {
        background: $surface-raised; color: $ink-soft; border: none; height: 1;
        padding: 0 1; min-width: 8;
    }
    Button:hover { background: $surface-active; color: $ink; }
    Button:focus { background: $accent; color: $accent-ink; text-style: bold; }
    Button.-primary { background: $accent; color: $accent-ink; }
    Button.-primary:focus { text-style: bold underline; }
    Button.-error { background: $surface-raised; color: $danger; }
    Button.-error:focus { background: $danger; color: $canvas; text-style: bold; }
    Button:disabled { color: $ink-muted; background: $surface; text-style: none; }
    .field Input, .field Select { width: 1fr; }
    # Padding on Select itself swallows the click that opens its overlay; indent inside instead.
    Input, Select { height: 1; border: none; padding: 0 1; overflow: hidden; scrollbar-size: 0 0; }
    Select { padding: 0; }
    Input:focus, Select:focus { background: $surface-active; }
    .dialog-actions { height: auto; margin-top: 1; }
    .dialog-actions Button { margin-right: 1; }
    .sidebar-actions { height: 1; }
    .sidebar-actions Button { min-width: 8; width: 1fr; }
    #action-delete { width: 1fr; }
    .compact #sidebar { width: 26; min-width: 20; }
    .compact #detail-pane { padding: 1 1 0 1; }
    .compact #action-bar Button { min-width: 6; margin-right: 0; }
    .compact .panel { width: 96%; padding: 1; }
    #transform-content { height: 1fr; }
    #transform-preview-col > Static:first-child { height: 1fr; }
    .compact #transform-content { layout: grid; grid-size: 2; grid-rows: 1fr 2fr; }
    .compact #transform-preview-col { column-span: 2; width: 1fr; margin-left: 0; }
    .compact #transform-intro-view { height: 2; max-height: 2; margin: 0; }
    .compact #transform-params-view { height: 1; margin: 0; }
    .compact #transform-preview-col > Static:last-child { height: 1; overflow: hidden; }
    .compact #transform-cat-col { width: 1fr; min-width: 12; }
    .compact #transform-preset-col { width: 1fr; min-width: 18; }
    .compact .sidebar-actions Button { padding: 0; }
    .compact .sidebar-actions #action-providers { width: 13; }
    """
    BINDINGS = [
        Binding("q", "quit", t("Quit", "退出")),
        Binding("c", "cache", t("Cache", "缓存")),
        Binding("v", "providers", "Providers"),
        Binding("p", "providers", "Providers", show=False),
        Binding("s", "superres", t("Local models", "本地模型")),
        Binding("n", "new_session", t("New session", "新建会话")),
        Binding("g", "generate", t("Generate", "生成")),
        Binding("m", "edit", t("Edit", "编辑")),
        Binding("x", "delete", t("Delete session", "删除会话")),
        Binding("d", "delete_artifact", t("Delete image", "删除图片")),
        Binding("delete", "delete_artifact", t("Delete image", "删除图片"), show=False),
        Binding("f", "fork", t("Fork session", "分支会话")),
        Binding("e", "export", t("Export session", "导出会话")),
        Binding("t", "transform", t("Transform", "变换")),
        Binding("o", "open_image", t("Open image", "打开图片")),
        Binding("O", "open_folder", t("Open folder", "打开目录")),
        Binding("[", "previous_artifact", t("Previous artifact", "上一张产物")),
        Binding("]", "next_artifact", t("Next artifact", "下一张产物")),
        Binding("r", "refresh", t("Refresh", "刷新")),
        Binding("colon", "commands", t("All commands", "全部命令")),
        Binding("?", "help", t("Help", "帮助")),
        Binding("l", "toggle_language", t("Switch language", "切换语言")),
    ]

    def __init__(self, deps: Deps, *, workspace_label: str = "") -> None:
        super().__init__()
        self.deps = deps
        self.workspace_label = workspace_label
        self.register_theme(Theme(
            name="imagen-darkroom", primary="#e98cab", secondary="#b0b7c2",
            accent="#e98cab", foreground="#f4f1ef", background="#090b10",
            surface="#11151d", panel="#181e28", success="#81c995",
            warning="#f2c879", error="#ed8b8b", dark=True,
            variables={"footer-key-foreground": "#e98cab", "input-cursor-background": "#e98cab"},
        ))
        self.theme = "imagen-darkroom"
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
        self._name_width = 12
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
                yield Horizontal(
                    Button(t("New", "新建"), id="action-new"),
                    Button(t("Providers", "端点"), id="action-providers"),
                    classes="sidebar-actions",
                )
                yield self.sessions
                yield Button(t("Delete session", "删除会话"), id="action-delete", variant="error")
            with Vertical(id="detail-pane"):
                yield self.detail
                yield self.meta
                with Vertical(id="preview-frame"):
                    yield self.preview
                yield Horizontal(
                    Button(t("Generate", "生成"), id="action-generate", classes="-primary"),
                    Button(t("Edit", "编辑"), id="action-edit"),
                    Button(t("Transform", "变换"), id="action-transform"),
                    Button(t("Open", "打开"), id="action-open"),
                    Static(id="action-gap"),
                    Button(t("‹", "‹"), id="action-prev", tooltip=t("Previous image", "上一张")),
                    Button(t("›", "›"), id="action-next", tooltip=t("Next image", "下一张")),
                    id="action-bar",
                )
                yield self.film
                yield self.status
        yield Static(id="keybar")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {
            "action-new": self.action_new_session,
            "action-providers": self.action_providers,
            "action-delete": self.action_delete,
            "action-generate": self.action_generate,
            "action-edit": self.action_edit,
            "action-transform": self.action_transform,
            "action-open": self.action_open_image,
            "action-prev": self.action_previous_artifact,
            "action-next": self.action_next_artifact,
        }
        action = actions.get(str(event.button.id))
        if action is not None:
            action()

    def _keybar_entries(self) -> list[tuple[str, str]]:
        keys = _KEYBAR_KEYS
        if self.current is None:
            keys = [entry for entry in keys if entry[0] in _KEYBAR_EMPTY_WORKSPACE]
        return [(key, t(*labels)) for key, labels in [*keys, *_KEYBAR_TAIL]]

    def _render_keybar(self) -> None:
        """Re-fit the legend after a resize or a selection change."""
        width = (self.size.width or 80) - 2  # keybar padding
        self.query_one("#keybar", Static).update(_keybar(self._keybar_entries(), width))

    def action_toggle_language(self) -> None:
        from imagen_cli import i18n

        target = "en" if i18n.is_chinese() else "zh-CN"
        i18n.set_language(target)
        try:
            i18n.save_language(target)
        except OSError:
            pass
        self._add_session_columns()
        self.refresh_rows()
        self._render_keybar()
        labels = {
            "new": t("New", "新建"), "providers": t("Providers", "端点"),
            "delete": t("Delete session", "删除会话"),
            "generate": t("Generate", "生成"), "edit": t("Edit", "编辑"),
            "transform": t("Transform", "变换"), "open": t("Open", "打开"),
        }
        for identity, label in labels.items():
            self.query_one(f"#action-{identity}", Button).label = label
        self._say(t("Language: English", "语言：中文"), "info")

    def on_mount(self) -> None:
        self.title = "Imagen"
        self.sessions.focus()
        self.sessions.cursor_type = "row"
        self._add_session_columns()
        self.refresh_rows()
        self._render_keybar()

    def _add_session_columns(self) -> None:
        """Narrow fixed columns: the last header stays visible and long names truncate.

        Compact drops the turn count, which costs the most and tells the least; the table
        width is the sidebar minus its padding and scrollbar, cells pad by one per side.
        """
        compact = self.has_class("compact")
        version = max(5, cell_len(t("Version", "版本")))
        turns = max(5, cell_len(t("Turns", "轮次")))
        cells = (22 if compact else 28) - 2 * (2 if compact else 3)
        self._name_width = cells - version if compact else cells - turns - version
        self.sessions.clear(columns=True)
        if compact:
            self.sessions.add_column(t("Session", "会话"), key="name", width=self._name_width)
            self.sessions.add_column(t("Version", "版本"), key="version", width=version)
        else:
            self.sessions.add_column(t("Session", "会话"), key="name", width=self._name_width)
            self.sessions.add_column(t("Turns", "轮次"), key="turns", width=turns)
            self.sessions.add_column(t("Version", "版本"), key="version", width=version)

    def _say(self, message: str, tone: str = "muted") -> None:
        self.status.update(Text(message, style=_TONES[tone]))

    def refresh_rows(self) -> None:
        self.sessions.clear()
        sessions = self._session_rows()
        compact = self.has_class("compact")
        for row in sessions:
            turns = len(queries.iter_all(self.deps.repositories, "turns", session_id=row["id"]))
            self.sessions.add_row(
                _ellipsize(_sanitize(row["name"]), self._name_width),
                *([str(row["version"])] if compact else [str(turns), str(row["version"])]),
                key=row["id"],
            )
        identities = [str(row["id"]) for row in sessions]
        if self.current not in identities:
            self.current = identities[0] if identities else None
        if not identities:
            self._preview_request_id += 1
            self._sync_actions()
            self.detail.update(t("No sessions yet", "尚无会话"))
            self.meta.update("")
            self.film_items = []
            self.film_index = -1
            self.film.update("")
            # One instruction, in the panel that has room for it; the status line stays for results.
            self.preview.update(
                Text(
                    t(
                        "No sessions yet\n\nPress n to create one, then v to pick the endpoint",
                        "尚无会话\n\n按 n 新建，v 选择端点与模型",
                    ),
                    justify="center",
                )
            )
            self._say("")
            self._render_keybar()
            return
        self.sessions.move_cursor(row=identities.index(str(self.current)))
        self.show_detail()
        self._render_keybar()

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

    def on_resize(self, event: events.Resize) -> None:
        compact = event.size.width < 110
        if compact != self.has_class("compact"):
            # The column set changes with the sidebar width, so the rows are rebuilt too.
            self.set_class(compact, "compact")
            self._add_session_columns()
            self.refresh_rows()
        for screen in self.screen_stack:
            screen.set_class(compact, "compact")
        self._render_keybar()
        self.show_detail()

    def _sync_actions(self) -> None:
        for identity in ("edit", "transform", "open"):
            self.query_one(f"#action-{identity}", Button).disabled = not self.film_items
        self.query_one("#action-prev", Button).disabled = self.film_index <= 0
        self.query_one("#action-next", Button).disabled = (
            self.film_index < 0 or self.film_index >= len(self.film_items) - 1
        )
        self.query_one("#action-delete", Button).disabled = self.current is None

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

    def _load_artifacts(self, session: str | None = None) -> list[dict[str, Any]]:
        """One entry per published output, oldest turn first."""
        identity = session or self.current
        if identity is None:
            return []
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        turns = sorted(
            queries.iter_all(self.deps.repositories, "turns", session_id=identity),
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
            self.deps.repositories, "artifact_links", session_id=identity
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
        self._sync_actions()
        if not self.film_items:
            self.film.update("")
            return
        # Keep a window around the selection so a long session stays one line.
        width = self.film.size.width or 0
        limit = max(3, min(9, (width - 14) // 5)) if width else 9
        start = max(0, min(len(self.film_items) - limit, self.film_index - limit // 2))
        window = self.film_items[start : start + limit]
        text = Text()
        text.append(t("Artifacts ", "产物 "), style="#b0b7c2")
        text.append(f"{self.film_index + 1}/{len(self.film_items)}  ", style="#b0b7c2")
        if start:
            text.append("… ", style="#7f8998")
        for offset, item in enumerate(window):
            index = start + offset
            if index == self.film_index:
                style = "bold #24131b on #e98cab"
            elif item["state"] == "failed":
                style = "#ed8b8b"
            elif item["state"] in {"queued", "dispatching", "running"}:
                style = "#f2c879"
            else:
                style = "#b0b7c2"
            if offset:
                text.append(" ")
            text.append(f" {index + 1:02d} ", style=style)
        if start + limit < len(self.film_items):
            text.append(" …", style="#7f8998")
        self.film.update(text)

    def _render_preview(self) -> None:
        self._preview_request_id += 1
        if not self.film_items or not 0 <= self.film_index < len(self.film_items):
            self.preview.update(
                Text(
                    t(
                        "No artifacts yet\n\nPress g to generate the first image",
                        "尚无产物\n\n按 g 生成第一张图",
                    ),
                    justify="center",
                )
            )
            return
        item = self.film_items[self.film_index]
        path = cast(Path, item["path"])
        artifact = self.deps.repositories.get("artifacts", str(item["id"]))
        if artifact is None:
            self.preview.update(Text(t("Artifact unavailable", "产物不可用"), justify="center"))
            return
        width, height = artifact.get("width"), artifact.get("height")
        size = f"{width}×{height}" if width and height else ""
        self.meta.update(" · ".join(part for part in (self.meta_base, size) if part))
        if not preview.supported():
            self.preview.update(preview.describe(artifact, path))
            return

        frame = self.query_one("#preview-frame")
        cols = max(1, frame.content_size.width)
        rows = max(1, frame.content_size.height)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0

        cache_key = (str(path), cols, rows, mtime)
        if cache_key in self._preview_cache:
            self.preview.update(Text.from_ansi(self._preview_cache[cache_key]))
            return

        req_id = self._preview_request_id
        # 轻量占位符，UI 立即响应
        self.preview.update(
            Text(
                t(f"\n\nLoading image preview…\n{size}", f"\n\n正在加载图像预览…\n{size}"),
                justify="center",
            )
        )
        self._load_preview_async(req_id, path, cols, rows, mtime)

    @work(thread=True, group="preview_render", exclusive=True)
    def _load_preview_async(
        self, req_id: int, path: Path, cols: int, rows: int, mtime: float
    ) -> None:
        try:
            ansi_str = preview.ansi_blocks(path, cols=cols, rows=rows)
        except Exception:
            self.call_from_thread(self._on_preview_failed, req_id, str(path))
            return
        self.call_from_thread(self._on_preview_loaded, req_id, ansi_str, path, cols, rows, mtime)

    def _on_preview_failed(self, req_id: int, path: str) -> None:
        if req_id == self._preview_request_id:
            self.preview.update(Text(t("Preview unavailable", "预览不可用") + "\n" + path))

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
        self.detail.update(
            t(
                f"{_sanitize(row['name'])} · version {row['version']}",
                f"{_sanitize(row['name'])} · 版本 {row['version']}",
            )
        )
        profile, model = self._creation_defaults(row)
        model = model.rsplit("/", 1)[-1]
        # The endpoint reads as a name; its id is 36 cells of noise on a one-line summary.
        stored = self.deps.repositories.get("profiles", profile) if profile else None
        self.meta_base = " · ".join(
            part for part in (str((stored or {}).get("name") or profile), model) if part
        ) or t("No endpoint or model selected yet", "尚未选择端点与模型")
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

    def _push_plan(self, status: str, data: dict[str, Any], label: str | None = None) -> None:
        label = label or t("Generate", "生成")
        plan = data.get("plan") or {}
        if status not in {"approval_required", "planned"} or not plan.get("id"):
            tone = "ok" if status in {"succeeded", "accepted"} else "error"
            reason = error_message(data_error(data))
            self._say(
                t(f"{label} result: {status}", f"{label}结果：{status}")
                + (f" · {reason}" if reason else ""),
                tone,
            )
            return

        def finished(changed: bool | None) -> None:
            if changed:
                self.refresh_rows()
                self.show_detail()
                self._select_artifact(len(self.film_items) - 1)
                self._say(
                    t("Execution finished · latest artifacts loaded", "执行完成 · 已载入最新产物"),
                    "ok",
                )

        self.push_screen(PlanScreen(self.deps, plan), finished)

    def action_commands(self) -> None:
        def chosen(command: str | None) -> None:
            if command is not None:
                self._open_command(command)

        self.push_screen(CommandPaletteScreen(), chosen)

    def _open_command(self, command: str) -> None:
        if self.deps.execute is None:
            self._say(t("Executor unavailable", "执行器不可用"), "error")
            return
        if command == "worker start":

            def confirmed(answer: bool | None) -> None:
                if answer is True:
                    self._open_command_form(command)

            self.push_screen(
                ConfirmScreen(
                    t(
                        "Start the worker? It consumes queued async jobs and can spend money.",
                        "启动 worker？它会消费已排队的异步任务，可能产生费用。",
                    )
                ),
                confirmed,
            )
            return
        self._open_command_form(command)

    def _open_command_form(self, command: str) -> None:
        fields, optional = command_fields(command)
        controls = _control_fields(command)
        control_names = {name for name, _, _ in controls}
        fields = [field for field in fields if field[0] not in control_names] + controls
        optional |= control_names
        if not _needs_form(command):
            self._run_command(command, {})
            return

        def done(values: dict[str, str] | None) -> None:
            if values is None:
                return
            try:
                arguments = _convert_arguments(
                    command, {key: value for key, value in values.items()
                              if key not in control_names}
                )
                options = _control_options(self.deps.options, _convert_controls(values))
            except (ValueError, TypeError) as error:
                self._say(t("Parameter error: ", "参数错误：") + _sanitize(error), "error")
                return
            self._run_command(command, arguments, options)

        choices: dict[str, list[tuple[str, str]]] = {}
        for name, spec in discover(command)["input_schema"].get("properties", {}).items():
            if "enum" in spec:
                choices[name] = [(str(value), str(value)) for value in spec["enum"]]
            elif spec.get("type") == "boolean" or isinstance(spec.get("const"), bool):
                choices[name] = [("true", "true"), ("false", "false")]
        for name in control_names - {"timeout"}:
            choices[name] = [("true", "true"), ("false", "false")]
        names = {name for name, _, _ in fields}
        choices, multiple, custom = self._command_choices(command, choices, names)
        title = t(f"Command {command}", f"命令 {command}")
        if command == "sheet":
            self.push_screen(
                SheetForm(title, fields, choices=choices, multiple=multiple, custom=custom,
                          artifacts_for=self._sheet_artifacts, optional=optional), done
            )
            return
        if command == "models use":
            # A model id belongs to one profile, so the list follows the profile field.
            choices["id"] = []
            custom.add("id")
            self.push_screen(
                ModelUseForm(title, fields, choices=choices, custom=custom,
                             models=self._declared_models(), optional=optional), done
            )
            return
        self.push_screen(
            ChoiceForm(title, fields, choices=choices, multiple=multiple, custom=custom,
                       optional=optional), done
        )

    def _declared_models(self) -> dict[str, list[str]]:
        """Declared ids per profile: one source for the endpoint panel and the palette."""
        return {
            str(row["id"]): [
                str(model["model_id"])
                for model in queries.iter_all(
                    self.deps.repositories, "provider_models", profile_id=str(row["id"])
                )
            ]
            for row in queries.iter_all(self.deps.repositories, "profiles")
        }

    def _sheet_artifacts(self, session: str) -> list[tuple[str, str]]:
        """One session's outputs; the current session stands in while the field is empty."""
        return self._artifact_choices(session or None)

    def _session_rows(self) -> list[dict[str, Any]]:
        return [
            row
            for row in queries.iter_all(self.deps.repositories, "sessions")
            if row.get("deleted_at") is None
        ]

    def _session_choices(self) -> list[tuple[str, str]]:
        """Names the user recognises, ids the CLI resolves."""
        return [
            (f"{_sanitize(row['name'])} ({row['id']})", str(row["id"]))
            for row in self._session_rows()
        ]

    def _artifact_choices(self, session: str | None = None) -> list[tuple[str, str]]:
        """One session's outputs, numbered the way the film strip numbers them."""
        choices = []
        for index, item in enumerate(self._load_artifacts(session)):
            width, height = item["width"], item["height"]
            label = " ".join(
                part
                for part in (
                    f"{index + 1:02d}.",
                    _sanitize(item["id"]),
                    f"{width}×{height}" if width and height else "",
                    _sanitize(item["operation"]),
                )
                if part
            )
            choices.append((label, str(item["id"])))
        return choices

    def _command_choices(
        self,
        command: str,
        choices: dict[str, list[tuple[str, str]]],
        names: set[str],
    ) -> tuple[dict[str, list[tuple[str, str]]], dict[str, list[str]], set[str]]:
        """Candidates the CLI offers for this command, so both ends pick from one list.

        Commands that create an id (`profile add`) keep a free text field: nothing to pick.
        """
        multiple: dict[str, list[str]] = {}
        custom: set[str] = set()
        for name, candidates in (
            ("session", self._session_choices),
            ("artifact", self._artifact_choices),
        ):
            if name in names:
                known = candidates()
                if known:  # an empty list would leave nothing to pick and nothing to type
                    choices[name] = known
                    custom.add(name)
        if command in {"superres fetch", "superres benchmark"}:
            choices["model"] = superres.fetch_choices()
        if command == "transform":
            # Same two lists `imagen ops` prints; parameters are appended by hand.
            from imagen_cli.application.operator_catalog import browse_entries

            choices["op"] = [(name, name) for name in sorted(media_ops.OPS)]
            choices["preset"] = [
                (key, key)
                for key in dict.fromkeys(
                    entry["key"].split(" (", 1)[0] for entry in browse_entries()
                )
            ]
            custom |= {"op", "preset"}
        if "artifacts" in names:
            # A sheet's sources come from one session; an empty list would leave a field
            # with nothing to tick and nothing to type.
            known = self._artifact_choices()
            if known:
                choices["artifacts"] = known
                multiple["artifacts"] = [value for _, value in known]
        if command.startswith("skills "):
            labels, preselected = prompt_order()
            choices["target"] = [(label, label) for label in labels]
            multiple["target"] = preselected
        if command in _EXISTING_PROFILE_COMMANDS:
            choices["profile"] = [
                (
                    f"{row.get('name') or row['id']} · {row.get('provider', '')} ({row['id']})",
                    str(row["id"]),
                )
                for row in queries.iter_all(self.deps.repositories, "profiles")
            ]
        return choices, multiple, custom

    def _run_command(
        self,
        command: str,
        arguments: dict[str, Any],
        options: SharedOptions | None = None,
    ) -> None:
        if self.deps.execute is None:
            self._say(t("Executor unavailable", "执行器不可用"), "error")
            return
        self._say(t(f"Running {command}…", f"执行 {command}…"), "info")
        self._run_command_worker(command, arguments, options)

    @work(thread=True, group="command", exclusive=True)
    def _run_command_worker(
        self,
        command: str,
        arguments: dict[str, Any],
        options: SharedOptions | None = None,
    ) -> None:
        try:
            status, data = _executor(self.deps, options)(command, arguments)
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
            self._push_plan(status, data, t("Command", "命令"))
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

    def _creation_form(
        self, title: str, fields: list[tuple[str, str, str]], *, optional: set[str],
    ) -> CreationForm:
        profiles = queries.iter_all(self.deps.repositories, "profiles")
        models = self._declared_models()
        def fetch_models(identity: str) -> list[str]:
            from functools import partial

            from imagen_cli.application.model_catalog_service import fetch
            from imagen_cli.execution.configured import resolve_credential

            profile = next(row for row in profiles if row["id"] == identity)
            catalog = fetch(profile, send_credential=True, resolve_credential=partial(
                resolve_credential, private_store=self.deps.private_store,
            ))
            return [str(row["model_id"]) for row in catalog["models"]]

        # Resolve private references on the UI thread; the worker only performs HTTP.
        profiles = [self.deps.repositories.get("profiles", row["id"], include_private=True)
                    or row for row in profiles]
        return CreationForm(title, fields, profiles=profiles, models=models, optional=optional,
                            fetch_models=fetch_models, add_profile=self.action_providers)

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
                self._say(
                    t(
                        f"Session name already exists: {values['name']}",
                        f"会话名已存在：{values['name']}",
                    ),
                    "error",
                )
                return
            except Exception as error:  # noqa: BLE001 - surfaced in the panel
                self._say(t("Create failed: ", "新建失败：") + _sanitize(error), "error")
                return
            self.current = str(row["id"])
            self.refresh_rows()
            self.show_detail()

        self.push_screen(
            self._creation_form(
                t("New session", "新建会话"),
                [
                    ("name", t("session name", "会话名"), ""),
                    ("intent", t("intent (optional)", "意图（可留空）"), ""),
                    ("profile", t("profile (optional)", "配置（可留空）"), ""),
                    ("model", t("model (optional)", "模型（可留空）"), ""),
                ],
                optional={"intent", "profile", "model"},
            ),
            done,
        )

    def _pixel_default(self, identity: str, model: str) -> str:
        """Prefill a pixel size only where the route actually has that field."""
        row = self.deps.repositories.get("profiles", identity) if identity else None
        table = vocabulary.specs(
            str((row or {}).get("provider", "")),
            str((row or {}).get("family", "")),
            model or str((row or {}).get("model_default", "")),
        ) or ()
        return "1024x1024" if any(spec.name == "size" for spec in table) else ""

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

    def _run_creation(
        self,
        operation: str,
        arguments: dict[str, Any],
        label: str,
        options: SharedOptions | None = None,
    ) -> None:
        if self.deps.execute is None:
            self._say(t("Executor unavailable", "执行器不可用"), "error")
            return
        if self.planning:
            self._say(
                t("The previous item is still running; please wait", "上一项仍在处理，请稍候"),
                "info",
            )
            return
        self.planning = True
        self._say(t("Planning…", "规划中…"), "info")
        self._plan_creation(operation, arguments, label, options)

    @work(thread=True, group="creation", exclusive=True)
    def _plan_creation(
        self,
        operation: str,
        arguments: dict[str, Any],
        label: str,
        options: SharedOptions | None = None,
    ) -> None:
        try:
            status, data = _executor(self.deps, options)(operation, arguments)
        except Exception as error:  # noqa: BLE001 - surfaced in the panel
            self.call_from_thread(self._plan_failed, label, _sanitize(error))
            return
        self.call_from_thread(self._plan_ready, status, data, label)

    def _plan_failed(self, label: str, message: str) -> None:
        self.planning = False
        self._say(t(f"{label} failed: {message}", f"{label}失败：{message}"), "error")

    def _plan_ready(self, status: str, data: dict[str, Any], label: str) -> None:
        self.planning = False
        self._push_plan(status, data, label)

    def action_generate(self) -> None:
        if self.current is None:
            self._say(
                t("No sessions yet; press n to create one", "尚无会话；按 n 新建后再生成"), "info"
            )
            return
        session = self.deps.repositories.get("sessions", self.current)
        if session is None:
            return
        if self.deps.execute is None:
            self._say(t("Executor unavailable", "执行器不可用"), "error")
            return

        def done(values: dict[str, str] | None) -> None:
            if not values or self.deps.execute is None:
                return
            arguments = _creation_arguments(values, "generate", session["name"])
            options = _control_options(self.deps.options, _convert_controls(values))
            self._run_creation("generate", arguments, t("Generate", "生成"), options)

        profile_default, model_default = self._creation_defaults(session)
        self.push_screen(
            self._creation_form(
                t("Generate image", "生成图片"),
                [
                    ("prompt", t("prompt", "提示词"), ""),
                    ("profile", t("profile (optional)", "配置（可留空）"), profile_default),
                    ("model", t("model (optional)", "模型（可留空）"), model_default),
                    ("count", t("count", "数量"), "1"),
                    (
                        "size",
                        t("pixels, e.g. 1536x864 or auto", "像素，如 1536x864 或 auto"),
                        self._pixel_default(profile_default, model_default),
                    ),
                    ("aspect_ratio", t("ratio, e.g. 16:9", "宽高比，如 16:9"), ""),
                    ("image_size", t("tier, e.g. 2K", "档位，如 2K"), ""),
                    ("async", "true | false", ""),
                ],
                optional={"profile", "model", "async", "size", "aspect_ratio", "image_size"},
            ),
            done,
        )

    def action_edit(self) -> None:
        found = self._current_artifact()
        if found is None or self.current is None:
            self._say(t("No artifact to edit", "没有可编辑的产物"), "info")
            return
        identity, _ = found
        session = self.deps.repositories.get("sessions", self.current)
        if session is None or self.deps.execute is None:
            return

        def done(values: dict[str, str] | None) -> None:
            if not values or self.deps.execute is None:
                return
            arguments = _creation_arguments(values, "edit", session["name"])
            arguments["inputs"] = ["artifact:" + identity]
            options = _control_options(self.deps.options, _convert_controls(values))
            self._run_creation("edit", arguments, t("Edit", "编辑"), options)

        profile_default, model_default = self._creation_defaults(session)
        self.push_screen(
            self._creation_form(
                t(f"Edit {identity}", f"编辑 {identity}"),
                [
                    ("prompt", t("prompt", "提示词"), ""),
                    ("profile", t("profile (optional)", "配置（可留空）"), profile_default),
                    ("model", t("model (optional)", "模型（可留空）"), model_default),
                    ("count", t("count", "数量"), "1"),
                    (
                        "size",
                        t("pixels, e.g. 1536x864 or auto", "像素，如 1536x864 或 auto"),
                        self._pixel_default(profile_default, model_default),
                    ),
                    ("aspect_ratio", t("ratio, e.g. 16:9", "宽高比，如 16:9"), ""),
                    ("image_size", t("tier, e.g. 2K", "档位，如 2K"), ""),
                    ("async", "true | false", ""),
                ],
                optional={"profile", "model", "async", "size", "aspect_ratio", "image_size"},
            ),
            done,
        )

    def action_cache(self) -> None:
        self.push_screen(CacheScreen(self.deps))

    def action_providers(self) -> None:
        self.push_screen(ProvidersScreen(self.deps))

    def action_superres(self) -> None:
        self.push_screen(SuperresScreen(self.deps, self._run_command))

    def action_refresh(self) -> None:
        self.refresh_rows()
        self.show_detail()

    def _open_current(self, folder: bool) -> None:
        found = self._current_artifact()
        if found is None or self.deps.open_artifact is None:
            self._say(t("This session has no artifact to open", "当前会话没有可打开的产物"), "info")
            return
        identity, path = found
        try:
            status, data = self.deps.open_artifact(identity, folder)
        except (ValueError, OSError) as error:
            self._say(t("Open failed: ", "打开失败：") + _sanitize(error), "error")
            return
        if status == "succeeded":
            self._say(
                t(f"Opened: {data.get('path', path)}", f"已打开：{data.get('path', path)}"), "ok"
            )
        else:
            self._say(t(f"Open result: {status}", f"打开结果：{status}"), "error")

    def action_open_image(self) -> None:
        self._open_current(False)

    def action_open_folder(self) -> None:
        self._open_current(True)

    def action_help(self) -> None:
        self.push_screen(
            HelpScreen(
                [
                    (
                        {"colon": ":"}.get(binding.key, binding.key),
                        t(
                            *_BINDING_TEXT.get(
                                binding.action,
                                (str(binding.description), str(binding.description)),
                            )
                        ),
                    )
                    for binding in self.BINDINGS
                    if isinstance(binding, Binding) and binding.show
                ]
            )
        )

    def action_delete(self) -> None:
        if self.current is None or self.deps.delete_session is None:
            self._say(t("No session to delete", "没有可删除的会话"), "info")
            return
        row = self.deps.repositories.get("sessions", self.current)
        if row is None:
            self._say(t("Session does not exist", "会话不存在"), "error")
            return
        name = _sanitize(row["name"])

        def finished(confirmed: bool | None) -> None:
            if not confirmed or self.current is None or self.deps.delete_session is None:
                return
            try:
                self.deps.delete_session(self.deps.repositories, self.current)
            except Exception as error:
                self._say(t("Delete failed: ", "删除失败：") + _sanitize(error), "error")
                return
            self.refresh_rows()
            self._say(t(f"Session deleted: {name}", f"已删除会话：{name}"), "ok")

        self.push_screen(
            ConfirmScreen(t(f"Delete session {name}?", f"删除会话 {name}？")),
            finished,
        )

    def action_delete_artifact(self) -> None:
        target = self._current_artifact()
        if target is None or self.current is None:
            self._say(
                t("The current session has no selected image", "当前会话没有选中的图片"), "info"
            )
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
                self._say(
                    t("Image deletion failed: ", "删除图片失败：") + _sanitize(error), "error"
                )
                return
            self.refresh_rows()
            self.show_detail()
            self._say(
                t(f"Removed image {art_id} from the session", f"已从会话中移除图片 {art_id}"), "ok"
            )

        self.push_screen(
            ConfirmScreen(
                t(
                    f"Remove image {num}/{total} from this session?",
                    f"从当前会话中移除第 {num}/{total} 张图片？",
                )
            ),
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
            self._say(t("No turns to fork", "没有可分支的轮次"), "info")
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
        self._say(
            t(f"Fork {status}: {_sanitize(name)}", f"分支 {status}: {_sanitize(name)}"),
            "ok" if status == "succeeded" else "error",
        )
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
                t(
                    f"Export {status}: {_sanitize(data.get('artifacts', 0))} artifacts",
                    f"导出 {status}: {_sanitize(data.get('artifacts', 0))} 个产物",
                ),
                "ok" if status == "succeeded" else "error",
            )

        self.push_screen(
            FormScreen(
                t("Export session", "导出会话"),
                [("destination", t("zip path", "zip 路径"), default)],
            ),
            done,
        )

    def action_transform(self) -> None:
        if self.current is None or self.deps.transform is None:
            return
        found = self._current_artifact()
        if found is None:
            self._say(t("No artifact to transform", "没有可变换的产物"), "info")
            return
        identity, path = found

        def finished(changed: bool | None) -> None:
            if changed:
                self.show_detail()
                self._select_artifact(len(self.film_items) - 1)

        self.push_screen(TransformScreen(self.deps, identity, self.current, path), finished)
