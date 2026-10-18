"""Terminal UI: browsing, confirmed writes and a local transform panel."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from PIL import Image
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Static

from imagen_cli.application import cache_service, media_ops
from imagen_cli.cli.human import render as human_render
from imagen_cli.cli.registry import COMMANDS, discover
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
    create_session: Callable[..., dict[str, Any]] | None = None
    execute: Callable[[str, dict[str, Any]], tuple[str, dict[str, Any]]] | None = None
    secret_ref: Callable[[str, str], str] | None = None
    private_store: PrivateStateStore | None = None
    workspace_root: Path | None = None

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
        from imagen_cli.cli.commands import models
        from imagen_cli.execution.configured import resolve_credential

        return models.refresh_(
            self.repositories, profile, send_credential=True,
            resolve_credential=resolve_credential,
        )

    def _models_add(self, profile: str, model: str) -> tuple[str, dict[str, Any]]:
        from imagen_cli.cli.commands import models

        return models.add_(self.repositories, profile, model)

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


class ConfirmScreen(ModalScreen[bool]):
    """Typed confirmation; Enter with the exact word authorizes the action."""

    def __init__(self, prompt: str, word: str) -> None:
        super().__init__()
        self.prompt = prompt
        self.word = word
        self.message = Static(prompt)
        self.field = Input(placeholder=word)

    def compose(self) -> ComposeResult:
        yield Vertical(self.message, self.field, Static("输入取消"), id="confirm")

    def on_mount(self) -> None:
        self.field.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value == self.word)


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
            Static(self.heading),
            *(Vertical(Static(name), field) for (name, _, _), field in zip(self.fields,
                                                                          self.inputs,
                                                                          strict=True)),
            Static("enter 提交 · esc 取消"),
            id="form",
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
                self.heading + " · 必填：" + "、".join(missing)
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
            Static("可回收空间"), self.table, self.status, self.field, id="cache"
        )

    def on_mount(self) -> None:
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
        with Horizontal():
            yield self.table
            with Vertical():
                yield self.view
                yield self.status

    def on_mount(self) -> None:
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


class ProvidersScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("a", "add", "新增"),
        Binding("u", "update", "更新"),
        Binding("m", "refresh_models", "刷新模型"),
        Binding("n", "add_model", "添加模型"),
        Binding("enter", "show_models", "查看模型"),
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
        self.detail = Static("")

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(
                "Providers · a 新增 · u 更新 · m 刷新模型 · n 添加模型 · enter 查看\n"
                "新增/更新时可直接填 API key，写入 workspace 私有存储；credential_ref 可留空"
            ),
            self.table, self.detail, self.status, id="providers",
        )

    def on_mount(self) -> None:
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
            except ValueError as error:
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
            (name, placeholder, _sanitize(profile.get(name, value)))
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
            except ValueError as error:
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

    def action_show_models(self) -> None:
        profile = self._selected()
        if profile is None:
            return
        rows = queries.iter_all(
            self.deps.repositories, "provider_models", profile_id=str(profile["id"])
        )
        self.detail.update(
            " · ".join(_sanitize(row["model_id"]) for row in rows) or "(尚未声明模型)"
        )

    def action_dismiss_screen(self) -> None:
        self.dismiss(False)


class PlanScreen(ModalScreen[bool]):
    """Show a frozen plan; approval and execution are separate keys."""

    BINDINGS = [
        Binding("a", "approve", "批准"),
        Binding("enter", "execute", "执行"),
        Binding("escape", "cancel", "取消"),
    ]

    def __init__(self, deps: Deps, plan: dict[str, Any]) -> None:
        super().__init__()
        self.deps = deps
        self.plan = plan
        self.approved = False
        self.status = Static("")

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(
                f"计划 {self.plan.get('id')}\n"
                f"操作 {self.plan.get('operation')} · 模型 {self.plan.get('model')}\n"
                f"摘要 {self.plan.get('plan_digest')}"
            ),
            self.status,
            Static("a 批准 · enter 执行 · esc 取消"),
            id="plan",
        )

    def on_mount(self) -> None:
        self.status.update("尚未批准")

    def action_approve(self) -> None:
        if self.deps.execute is None:
            self.status.update("执行器不可用")
            return
        status, _ = self.deps.execute(
            "plan approve",
            {"plan_id": self.plan["id"], "digest": self.plan["plan_digest"]},
        )
        if status in {"approved", "succeeded"}:
            self.approved = True
            self.status.update("已批准 · enter 执行")
        else:
            self.status.update(f"批准失败：{status}")

    def action_execute(self) -> None:
        if not self.approved:
            self.status.update("先按 a 批准")
            return
        if self.deps.execute is None:
            self.status.update("执行器不可用")
            return
        status, data = self.deps.execute("plan execute", {"plan_id": self.plan["id"]})
        job = data.get("job") or {}
        if status in {"succeeded", "accepted", "partial_success"}:
            self.status.update(f"执行完成：{job.get('state')} · {job.get('id')}")
            self.dismiss(True)
        else:
            self.status.update(f"执行结果：{status}")

    def action_cancel(self) -> None:
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
        yield Vertical(Static(f"{self.command} · {self.status}"), self.body, id="result")

    def on_mount(self) -> None:
        ok = self.status in {"succeeded", "accepted", "approved", "planned"}
        value = {
            "ok": ok, "status": self.status, "data": self.data,
            "error": None if ok else {"message": self.status},
        }
        self.body.update(Text.from_ansi(human_render(value, color=True)))

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
        self.table: DataTable[Any] = DataTable()
        self.status = Static("")

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("全部命令 · ↑↓ 选择 · enter 打开 · esc 关闭"),
            self.table, self.status, id="palette",
        )

    def on_mount(self) -> None:
        self.table.add_columns("命令", "说明")
        for name in self.names:
            self.table.add_row(_sanitize(name), _sanitize(COMMANDS[name].summary))
        self.table.can_focus = False
        self.table.move_cursor(row=0)
        self.set_focus(None)

    def _move(self, delta: int) -> None:
        row = min(len(self.names) - 1, max(0, self.table.cursor_row + delta))
        self.table.move_cursor(row=row)

    def action_cursor_up(self) -> None:
        self._move(-1)

    def action_cursor_down(self) -> None:
        self._move(1)

    def action_select(self) -> None:
        row = self.table.cursor_row
        if 0 <= row < len(self.names):
            self.dismiss(self.names[row])

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


class WorkbenchApp(App[None]):
    CSS = """
    #sessions { width: 40%; }
    #detail { width: 60%; }
    #preview { height: 55%; }
    #hint { height: auto; color: $text-muted; }
    """
    BINDINGS = [
        Binding("q", "quit", "退出"),
        Binding("c", "cache", "缓存"),
        Binding("v", "providers", "Providers"),
        Binding("p", "providers", "Providers"),
        Binding("n", "new_session", "新建会话"),
        Binding("g", "generate", "生成"),
        Binding("m", "edit", "编辑"),
        Binding("x", "delete", "删除会话"),
        Binding("f", "fork", "分支会话"),
        Binding("e", "export", "导出会话"),
        Binding("t", "transform", "变换"),
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
        self.preview = Static(id="preview")
        self.hint = Static(
            "g 生成 · m 编辑 · n 新建会话 · t 变换 · f 分支 · e 导出\n"
            ": 全部命令 · v/p providers · c 缓存 · x 删除 · r 刷新 · q 退出",
            id="hint",
        )
        self.current: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield self.sessions
            with Vertical(id="detail"):
                yield self.detail
                yield self.preview
                yield self.hint
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Imagen"
        self.sub_title = self.workspace_label
        self.sessions.add_columns("会话", "轮次", "版本")
        self.refresh_rows()

    def refresh_rows(self) -> None:
        self.sessions.clear()
        sessions = queries.iter_all(self.deps.repositories, "sessions")
        for row in sessions:
            if row.get("deleted_at") is not None:
                continue
            turns = len(queries.iter_all(self.deps.repositories, "turns", session_id=row["id"]))
            self.sessions.add_row(
                _sanitize(row["name"]), str(turns), str(row["version"]), key=row["id"],
            )
        if sessions:
            self.detail.update(f"{len(sessions)} 个会话")

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self.current = str(event.row_key.value)
        self.show_detail()

    def _current_artifact(self) -> tuple[str, Path] | None:
        if self.current is None:
            return None
        identities: list[str] = []
        turns = sorted(
            queries.iter_all(self.deps.repositories, "turns", session_id=self.current),
            key=lambda row: (row.get("created_at", ""), row["id"]),
        )
        for turn in turns:
            identities.extend(
                row["artifact_id"] for row in queries.iter_all(
                    self.deps.repositories, "artifact_references", turn_id=turn["id"]
                )
                if row.get("role") == "output"
            )
        identities.extend(
            row["artifact_id"] for row in queries.iter_all(
                self.deps.repositories, "artifact_links", session_id=self.current
            )
        )
        for identity in reversed(identities):
            path = self.deps.store.get(identity)
            if path is not None:
                return identity, path
        return None

    def show_detail(self) -> None:
        if self.current is None:
            return
        row = self.deps.repositories.get("sessions", self.current)
        if row is None:
            return
        self.detail.update(f"{_sanitize(row['name'])} · version {row['version']}")
        found = self._current_artifact()
        if found is None:
            self.preview.update("(无产物)")
            return
        identity, path = found
        artifact = self.deps.repositories.get("artifacts", identity)
        if artifact is None:
            self.preview.update("(产物不可用)")
            return
        if preview.supported():
            self.preview.update(Text.from_ansi(preview.ansi_blocks(path, cols=40, rows=12)))
        else:
            self.preview.update(preview.describe(artifact, path))

    def _push_plan(self, status: str, data: dict[str, Any]) -> None:
        plan = data.get("plan") or {}
        if status not in {"approval_required", "planned"} or not plan.get("id"):
            self.detail.update(f"生成：{status}")
            return

        def finished(changed: bool | None) -> None:
            if changed:
                self.refresh_rows()
                self.show_detail()

        self.push_screen(PlanScreen(self.deps, plan), finished)

    def action_commands(self) -> None:
        def chosen(command: str | None) -> None:
            if command is not None:
                self._open_command(command)

        self.push_screen(CommandPaletteScreen(), chosen)

    def _open_command(self, command: str) -> None:
        if self.deps.execute is None:
            self.detail.update("执行器不可用")
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
                self.detail.update("参数错误：" + _sanitize(error))
                return
            self._run_command(command, arguments)

        self.push_screen(FormScreen(f"命令 {command}", fields, optional=optional), done)

    def _run_command(self, command: str, arguments: dict[str, Any]) -> None:
        assert self.deps.execute is not None
        try:
            status, data = self.deps.execute(command, arguments)
        except Exception as error:  # noqa: BLE001
            self.push_screen(ResultScreen(command, "failed", {"message": str(error)}))
            return
        if data.get("plan") and status in {"approval_required", "planned"}:
            self._push_plan(status, data)
            return
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
            except Exception as error:  # noqa: BLE001 - surfaced in the panel
                self.detail.update("新建失败：" + _sanitize(error))
                return
            self.current = str(row["id"])
            self.refresh_rows()
            self.show_detail()

        self.push_screen(
            FormScreen(
                "新建会话",
                [("name", "会话名", ""), ("intent", "意图（可留空）", ""),
                 ("profile", "profile（可留空）", ""), ("model", "model（可留空）", "")],
                optional={"intent", "profile", "model"},
            ),
            done,
        )

    def action_generate(self) -> None:
        if self.current is None:
            self.detail.update("先选择一个会话")
            return
        session = self.deps.repositories.get("sessions", self.current)
        if session is None:
            return
        if self.deps.execute is None:
            self.detail.update("执行器不可用")
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
            try:
                status, data = self.deps.execute("generate", arguments)
            except Exception as error:  # noqa: BLE001
                self.detail.update("生成失败：" + _sanitize(error))
                return
            self._push_plan(status, data)

        self.push_screen(
            FormScreen(
                "生成图片",
                [("prompt", "提示词", ""),
                 ("profile", "profile（可留空）", str(session.get("default_profile_id") or "")),
                 ("model", "model（可留空）", str(session.get("default_model") or "")),
                 ("count", "数量", "1"), ("size", "尺寸", "1024x1024")],
                optional={"profile", "model"},
            ),
            done,
        )

    def action_edit(self) -> None:
        found = self._current_artifact()
        if found is None or self.current is None:
            self.detail.update("没有可编辑的产物")
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
            try:
                status, data = self.deps.execute("edit", arguments)
            except Exception as error:  # noqa: BLE001
                self.detail.update("编辑失败：" + _sanitize(error))
                return
            self._push_plan(status, data)

        self.push_screen(
            FormScreen(
                f"编辑 {identity}",
                [("prompt", "提示词", ""),
                 ("profile", "profile（可留空）", str(session.get("default_profile_id") or "")),
                 ("model", "model（可留空）", str(session.get("default_model") or "")),
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

    def action_help(self) -> None:
        self.detail.update(
            "enter 打开 · c 缓存 · v providers · x 删除 · f 分支 · e 导出 · t 变换 · q 退出"
        )

    def action_delete(self) -> None:
        if self.current is None or self.deps.delete_session is None:
            return
        row = self.deps.repositories.get("sessions", self.current)
        if row is None:
            return
        name = _sanitize(row["name"])

        def finished(confirmed: bool | None) -> None:
            if confirmed and self.current is not None and self.deps.delete_session is not None:
                self.deps.delete_session(self.deps.repositories, self.current)
                self.refresh_rows()

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
            self.detail.update("没有可分支的轮次")
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
        self.detail.update(f"分支 {status}: {_sanitize(name)}")
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
            self.detail.update(
                f"导出 {status}: {_sanitize(data.get('artifacts', 0))} 个产物"
            )

        self.push_screen(
            FormScreen("导出会话", [("destination", "zip 路径", default)]), done
        )

    def action_transform(self) -> None:
        if self.current is None or self.deps.transform is None:
            return
        found = self._current_artifact()
        if found is None:
            self.detail.update("没有可变换的产物")
            return
        identity, path = found

        def finished(changed: bool | None) -> None:
            if changed:
                self.show_detail()

        self.push_screen(
            TransformScreen(self.deps, identity, self.current, path), finished
        )
