"""Model-specific workbench using shared local jobs and progress."""

from __future__ import annotations

import asyncio
import math
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING, Any

from PIL import Image
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Input, ProgressBar, Select, Static

from imagen_cli.application import superres
from imagen_cli.cli.options import SharedOptions
from imagen_cli.cli.progress import label
from imagen_cli.cli.registry import discover
from imagen_cli.cli.renderers import data_error, error_message
from imagen_cli.i18n import t

from . import preview
from .modal import Modal

if TYPE_CHECKING:
    from .app import Deps


class ModelProcessScreen(Modal[bool]):
    BINDINGS = [
        Binding("enter", "apply", "Run"),
        Binding("f", "fetch", "Download"),
        Binding("escape", "cancel", "Close"),
    ]
    DEFAULT_CSS = """
    #model-process { width: 94%; max-width: 160; height: 92%; overflow: hidden; }
    #model-process > .panel-title { width: 1fr; height: 1; padding: 0; }
    #model-source-size { height: 1; color: $text-muted; }
    #model-content { height: 1fr; margin-top: 1; }
    #model-list { width: 46%; margin-right: 2; }
    #model-filter { height: 1; margin-bottom: 1; }
    #processing-models { height: 1fr; }
    #model-image-pane { width: 1fr; }
    #model-preview { height: 1fr; content-align: center middle; }
    #model-description { height: 6; overflow-y: auto; margin-top: 1; }
    #model-settings { height: 2; margin-top: 1; }
    .model-setting-row { height: 1; }
    .model-setting-row Static { width: auto; padding-right: 1; }
    .model-setting-row Select { width: 20; margin-right: 2; }
    .model-setting-row Input { width: 8; margin-right: 2; }
    #model-process #processing-progress { height: 1; }
    #model-status { height: 1; overflow: hidden; }
    ModelProcessScreen.compact #model-content { layout: vertical; margin-top: 0; }
    ModelProcessScreen.compact #model-list { width: 1fr; height: 6; margin-right: 0; }
    ModelProcessScreen.compact #model-filter { margin-bottom: 0; }
    ModelProcessScreen.compact #model-image-pane { layout: horizontal; height: 1fr; }
    ModelProcessScreen.compact #model-preview { width: 38%; height: 1fr; margin-right: 1; }
    ModelProcessScreen.compact #model-description { width: 1fr; height: 1fr; margin-top: 0; }
    ModelProcessScreen.compact #model-settings { margin-top: 0; }
    ModelProcessScreen.compact .model-setting-row Select { width: 17; margin-right: 1; }
    ModelProcessScreen.compact .model-setting-row Input { width: 6; margin-right: 1; }
    ModelProcessScreen.compact #model-process .dialog-actions { margin-top: 0; }
    """

    def __init__(self, deps: Deps, artifact: str, session: str, path: Path) -> None:
        super().__init__()
        self.deps, self.artifact, self.session, self.path = deps, artifact, session, path
        self.running = False
        self.cancelled = Event()
        self.rows: list[dict[str, Any]] = []
        self.current_model = ""
        self.job_id: str | None = None
        self.downloading = False
        self._editable_geometry = ("256", "16")
        self._fixed_geometry = False
        with Image.open(path) as image:
            self.source_size = image.size
        defaults = discover("superres run")["input_schema"]["properties"]
        self.table: DataTable[Any] = DataTable(id="processing-models")
        self.view = Static(id="model-preview")
        self.description = Static(id="model-description")
        self.dimensions = Static(id="model-source-size")
        self.status = Static(id="model-status")
        self.filter = Select([
            (t("All models", "全部模型"), "all"), ("ACG", "acg"),
            (t("Restoration", "修复"), "restoration"),
            (t("General", "通用"), "general"),
        ], value="all", allow_blank=False, id="model-filter")
        self.backend = Select([
            (t("Auto backend", "自动后端"), "auto"),
            *[(name.upper(), name) for name in superres.PROVIDERS],
        ], value="auto", allow_blank=False, id="model-backend")
        self.mode = Select([
            (t("Foreground", "前台"), "foreground"),
            (t("Background", "后台"), "background"),
        ], value="foreground", allow_blank=False, id="model-mode")
        self.tile = Input(str(defaults["tile"]["default"]), id="model-tile")
        self.overlap = Input(str(defaults["overlap"]["default"]), id="model-overlap")
        self.timeout = Input(str(defaults["timeout"]["default"]), id="model-timeout")

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel", id="model-process"):
            yield Static(t("Model processing", "模型处理"), classes="panel-title")
            yield self.dimensions
            with Horizontal(id="model-content"):
                with Vertical(id="model-list"):
                    yield self.filter
                    yield self.table
                with Vertical(id="model-image-pane"):
                    yield self.view
                    yield self.description
            with Vertical(id="model-settings"):
                yield Horizontal(Static(t("Backend", "后端")), self.backend,
                                 Static(t("Run", "执行")), self.mode, classes="model-setting-row")
                yield Horizontal(Static(t("Tile", "分块")), self.tile,
                                 Static(t("Overlap", "重叠")), self.overlap,
                                 Static(t("Timeout", "超时")), self.timeout,
                                 classes="model-setting-row")
            yield ProgressBar(total=None, show_eta=False, id="processing-progress")
            yield self.status
            yield Horizontal(
                Button(t("Download", "下载权重"), id="model-download"),
                Button(t("Run", "运行"), id="model-run", variant="primary"),
                Button(t("Close", "关闭"), id="model-close"),
                classes="dialog-actions",
            )

    def on_mount(self) -> None:
        self.set_class(self.app.size.width < 110, "compact")
        self.table.cursor_type = "row"
        self.table.add_column(t("Model", "模型"), key="model", width=27)
        self.table.add_column(t("Scale", "倍率"), width=5)
        self.table.add_column(t("Weights", "权重"), width=7)
        self.query_one("#processing-progress", ProgressBar).display = False
        self.refresh_models()
        self.table.focus()
        self.call_after_refresh(self.render_preview)

    def on_resize(self, event: events.Resize) -> None:
        self.set_class(event.size.width < 110, "compact")
        self.call_after_refresh(self.render_preview)

    def refresh_models(self) -> None:
        catalog = superres.catalog()
        selected = self.current_model
        category = self.filter.value
        self.rows = [row for row in catalog["models"]
                     if category == "all" or row.get("category", "general") == category]
        self.table.clear()
        for row in self.rows:
            weights = t("Ready", "已下载") if row["downloaded"] else t("Missing", "未下载")
            self.table.add_row(row["model"], f"{row['scale']}x", weights, key=row["model"])
        names = [row["model"] for row in self.rows]
        if names:
            self.table.move_cursor(row=names.index(selected) if selected in names else 0)
        self._describe()

    def _selected(self) -> dict[str, Any] | None:
        return self.rows[self.table.cursor_row] if self.rows else None

    def _describe(self) -> None:
        row = self._selected()
        if row is None:
            self.description.update(t("No models in this category", "此分类下没有模型"))
            return
        self.current_model = row["model"]
        scale = row["scale"]
        width, height = self.source_size
        self.dimensions.update(t(
            f"Original {width}x{height}  →  Output {width * scale}x{height * scale}",
            f"原图 {width}×{height}  →  输出 {width * scale}×{height * scale}",
        ))
        tasks = {"denoise": t("Denoise · same size", "降噪 · 保持尺寸"),
                 "dejpeg": t("JPEG repair · same size", "JPEG 修复 · 保持尺寸"),
                 "upscale": t("Upscale", "超分")}
        self.description.update(Text("\n".join([
            tasks.get(row["task"], row["task"]) + f" · {row['model']}",
            str(row.get("usage") or ""), str(row["license"]), str(row["source"]),
        ])))
        fixed = superres.MANIFEST[row["model"]].get("fixed_input")
        self.tile.disabled = self.overlap.disabled = bool(fixed) or self.running
        if fixed and not self._fixed_geometry:
            self._editable_geometry = (self.tile.value, self.overlap.value)
        if fixed:
            self.tile.value, self.overlap.value = str(fixed), "0"
        elif self._fixed_geometry:
            self.tile.value, self.overlap.value = self._editable_geometry
        self._fixed_geometry = bool(fixed)
        self.query_one("#model-download", Button).disabled = (
            self.running or row["downloaded"] or not row["downloadable"]
        )
        self.status.update(t("Preview: original image", "预览：原图"))

    def render_preview(self) -> None:
        try:
            with Image.open(self.path) as image:
                thumbnail = image.convert("RGBA" if "A" in image.getbands() else "RGB")
                cols = max(1, min(96, self.view.content_size.width))
                rows = max(1, min(30, self.view.content_size.height))
                thumbnail.thumbnail((cols, rows * 2))
                self.view.update(Text.from_ansi(
                    preview.ansi_image(thumbnail, cols=cols, rows=rows),
                ))
        except (OSError, ValueError) as error:
            self.view.update(t("Preview unavailable", "预览不可用") + f"\n{error}")

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select is self.filter and self.is_mounted and not self.running:
            self.refresh_models()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        event.stop()
        if not self.running:
            self._describe()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        self.action_apply()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "model-run":
            self.action_apply()
        elif event.button.id == "model-download":
            self.action_fetch()
        elif event.button.id == "model-close":
            self.action_cancel()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.focus_next()

    def _arguments(self) -> dict[str, Any]:
        row = self._selected()
        if row is None:
            raise ValueError(t("Choose a model", "请选择模型"))
        tile, overlap = int(self.tile.value), int(self.overlap.value)
        timeout = float(self.timeout.value)
        if tile < 4 or not 0 <= overlap < tile:
            raise ValueError(t("Overlap must be smaller than tile size", "重叠必须小于分块尺寸"))
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError(t("Timeout must be zero or positive", "超时必须为零或正数"))
        args: dict[str, Any] = {"artifact": self.artifact, "session": self.session,
                                "model": row["model"], "tile": tile, "overlap": overlap,
                                "timeout": timeout}
        if self.backend.value != "auto":
            args["backend"] = self.backend.value
        return args

    def action_apply(self) -> None:
        from .app import ConfirmScreen

        if self.running:
            return
        row = self._selected()
        if row is None:
            return
        if not row["downloaded"]:
            self.status.update(t("Download the selected weights first", "请先下载所选权重"))
            return
        try:
            args = self._arguments()
        except ValueError as error:
            self.status.update(str(error))
            return

        def confirmed(answer: bool | None) -> None:
            if answer and self.is_mounted and not self.running:
                self._start({**args, "accept": True})

        self.app.push_screen(ConfirmScreen(t(
            f"Run {row['model']}? License: {row['license']}\nSource: {row['source']}",
            f"运行 {row['model']}？许可：{row['license']}\n来源：{row['source']}",
        )), confirmed)

    def _set_running(self, running: bool) -> None:
        self.running = running
        if not self.app.is_running or not self.status.is_attached:
            return
        for widget in (self.filter, self.backend, self.mode, self.tile, self.overlap, self.timeout):
            widget.disabled = running
        self.table.disabled = running
        self.query_one("#model-run", Button).disabled = running
        self.query_one("#model-download", Button).disabled = running
        self.query_one("#model-close", Button).label = (
            t("Cancel", "取消") if running else t("Close", "关闭")
        )
        if not running:
            self._describe()

    def _processing_progress(self, value: dict[str, Any]) -> None:
        if self.app.is_running and self.status.is_attached:
            self.query_one("#processing-progress", ProgressBar).update(
                total=value.get("total"), progress=value.get("completed", 0),
            )
            self.status.update(label(value))

    def _start(self, args: dict[str, Any]) -> None:
        from imagen_cli.application import local_processing, postprocess_service

        from .app import ConfirmScreen

        try:
            params = {key: args[key] for key in ("model", "tile", "overlap", "accept", "backend")
                      if key in args}
            pipeline = [{"op": "superres", "params": params}]
            postprocess_service.budget_preflight(self.path, pipeline, args.get("budget_approval"))
            if self.mode.value == "background":
                from .app import _executor

                options = (self.deps.options or SharedOptions()).model_copy(
                    update={"async_": True, "wait": False},
                )
                status, result = _executor(self.deps, options)("superres run", args)
                if status == "accepted":
                    self.dismiss(True)
                else:
                    self.status.update(error_message(data_error(result)) or status)
                return
            if self.deps.workspace_root is None:
                assert self.deps.transform is not None
                self.dismiss(bool(self.deps.transform(self.artifact, pipeline, self.session)))
                return
            job = local_processing.submit(self.deps.repositories, self.deps.store,
                                          self.artifact, pipeline, session_id=self.session,
                                          timeout=args["timeout"],
                                          budget_approval=args.get("budget_approval"))
        except postprocess_service.BudgetApprovalRequired as error:
            frozen = {**args, "budget_approval": error.report["digest"]}
            self.app.push_screen(ConfirmScreen(str(error)),
                                 lambda answer: self._start(frozen) if answer else None)
            return
        except (ValueError, OSError) as error:
            self.status.update(str(error))
            return
        self.job_id = job["id"]
        self.cancelled.clear()
        self._set_running(True)
        self.query_one("#processing-progress", ProgressBar).display = True
        self.run_worker(self._run_job(), exclusive=True)

    async def _run_job(self) -> None:
        from imagen_cli.application import local_processing
        from imagen_cli.application.container import Container

        loop = asyncio.get_running_loop()
        root, job_id = str(self.deps.workspace_root), self.job_id
        assert job_id is not None

        def progress(value: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(self._processing_progress, value)

        def run() -> tuple[str, dict[str, Any]]:
            with Container(workspace=root) as container:
                return local_processing.run(container.repositories, container.artifact_store,
                                            job_id, cancelled=self.cancelled, progress=progress)

        try:
            status, result = await asyncio.to_thread(run)
        except (OSError, ValueError, RuntimeError) as error:
            self.status.update(str(error))
        else:
            if status == "succeeded":
                self.dismiss(True)
                return
            self.status.update(error_message(data_error(result)) or status)
        finally:
            self.running = False
            if self.is_mounted:
                message = self.status.content
                self._set_running(False)
                self.status.update(message)

    def action_fetch(self) -> None:
        from .app import ConfirmScreen

        row = self._selected()
        if self.running or row is None or row["downloaded"] or not row["downloadable"]:
            return
        model = row["model"]
        def confirmed(answer: bool | None) -> None:
            if answer and self.is_mounted and not self.running:
                self._set_running(True)
                self.downloading = True
                self.query_one("#model-close", Button).label = t("Close", "关闭")
                self.status.update(t(
                    "Downloading weights; closing this page continues the download",
                    "下载权重中；关闭此页后下载继续",
                ))
                self.app.run_worker(lambda: self._download(model), thread=True,
                                    group="model-download")

        self.app.push_screen(ConfirmScreen(t(
            f"Download {model}? License: {row['license']}\nSource: {row['source']}",
            f"下载 {model}？许可：{row['license']}\n来源：{row['source']}",
        )), confirmed)

    def _download(self, model: str) -> None:
        from .app import _executor

        app = self.app
        try:
            status, result = _executor(self.deps)(
                "superres fetch", {"model": model, "accept": True},
            )
            message = error_message(data_error(result)) or status
        except (OSError, ValueError, RuntimeError) as error:
            message = str(error)
        try:
            app.call_from_thread(self._downloaded, message)
        except RuntimeError:
            pass  # The app may exit before the download thread finishes.

    def _downloaded(self, message: str) -> None:
        if self.app.is_running and self.status.is_attached and self in self.app.screen_stack:
            self.downloading = False
            self.query_one("#model-close", Button).disabled = False
            self._set_running(False)
            self.refresh_models()
            self.status.update(message)

    def action_cancel(self) -> None:
        if self.downloading:
            self.dismiss(False)
            return
        if self.running:
            self.cancelled.set()
            self.status.update(t("Canceling processing", "正在取消处理"))
            return
        self.dismiss(False)

    def on_unmount(self) -> None:
        self.cancelled.set()
