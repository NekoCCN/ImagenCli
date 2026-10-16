"""Terminal UI: read-only browsing plus two-phase destructive actions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Static

from imagen_cli.application import cache_service
from imagen_cli.storage import queries
from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.repositories import Repositories

from . import preview


@dataclass
class Deps:
    repositories: Repositories
    store: ArtifactStore
    audit: Callable[..., list[cache_service.Entry]] = cache_service.audit
    purge: Callable[..., dict[str, Any]] = cache_service.purge
    delete_session: Callable[[Repositories, str], dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        if self.delete_session is None:
            from imagen_cli.application.cleanup_service import delete_session

            self.delete_session = delete_session


def _sanitize(value: object) -> str:
    text = str(value)
    return "".join(char for char in text if char.isprintable())


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
        self.entries = self.deps.audit(self.deps.repositories, self.deps.store)
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
        result = self.deps.purge(self.deps.repositories, self.deps.store, self.digest)
        self.status.update(f"已删除 {result['deleted_bytes']} 字节")
        self.dismiss(True)

    def action_dismiss_screen(self) -> None:
        self.dismiss(False)


class ProvidersScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "dismiss_screen", "关闭")]

    def __init__(self, deps: Deps) -> None:
        super().__init__()
        self.deps = deps
        self.table: DataTable[Any] = DataTable()

    def compose(self) -> ComposeResult:
        yield Vertical(Static("Providers"), self.table, id="providers")

    def on_mount(self) -> None:
        self.table.add_columns("id", "provider", "family", "model", "revision")
        for row in queries.iter_all(self.deps.repositories, "profiles"):
            self.table.add_row(
                _sanitize(row["id"]), _sanitize(row["provider"]), _sanitize(row["family"]),
                _sanitize(row["model_default"]), str(row["revision"]),
            )

    def action_dismiss_screen(self) -> None:
        self.dismiss(False)


class WorkbenchApp(App[None]):
    CSS = """
    #sessions { width: 40%; }
    #detail { width: 60%; }
    #preview { height: 60%; }
    """
    BINDINGS = [
        Binding("q", "quit", "退出"),
        Binding("c", "cache", "缓存"),
        Binding("v", "providers", "Providers"),
        Binding("x", "delete", "删除会话"),
        Binding("r", "refresh", "刷新"),
        Binding("?", "help", "帮助"),
    ]

    def __init__(self, deps: Deps, *, workspace_label: str = "") -> None:
        super().__init__()
        self.deps = deps
        self.workspace_label = workspace_label
        self.sessions: DataTable[Any] = DataTable(id="sessions")
        self.detail = Static(id="detail")
        self.preview = Static(id="preview")
        self.current: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield self.sessions
            with Vertical(id="detail"):
                yield self.detail
                yield self.preview
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

    def show_detail(self) -> None:
        if self.current is None:
            return
        row = self.deps.repositories.get("sessions", self.current)
        if row is None:
            return
        self.detail.update(f"{_sanitize(row['name'])} · version {row['version']}")
        links = queries.iter_all(self.deps.repositories, "artifact_links", session_id=self.current)
        if not links:
            self.preview.update("(无产物)")
            return
        artifact = self.deps.repositories.get("artifacts", links[-1]["artifact_id"])
        if artifact is None:
            self.preview.update("(产物不可用)")
            return
        path = self.deps.store.get(artifact["id"])
        self.preview.update(preview.describe(artifact, path))

    def action_cache(self) -> None:
        self.push_screen(CacheScreen(self.deps))

    def action_providers(self) -> None:
        self.push_screen(ProvidersScreen(self.deps))

    def action_refresh(self) -> None:
        self.refresh_rows()
        self.show_detail()

    def action_help(self) -> None:
        self.detail.update("enter 打开 · c 缓存 · v providers · x 删除 · r 刷新 · q 退出")

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
