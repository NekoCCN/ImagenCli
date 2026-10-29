"""Progress presentation: human terminal on stderr, structured events for JSONL."""

from __future__ import annotations

from typing import Any

from imagen_cli.i18n import t

STAGES = {
    "queued": ("Queued", "排队中"),
    "preparing": ("Preparing", "准备中"),
    "loading_model": ("Loading model", "加载模型"),
    "processing": ("Processing", "处理中"),
    "encoding": ("Encoding", "编码中"),
    "saving": ("Saving", "保存中"),
    "completed": ("Completed", "已完成"),
    "running": ("Waiting for result", "等待结果"),
}


def label(value: dict[str, Any]) -> str:
    stage = str(value.get("stage") or "running")
    text = t(*STAGES[stage]) if stage in STAGES else stage
    operation = value.get("operation")
    if operation:
        text += f" · {operation}"
    if value.get("total") is not None:
        unit = {"steps": t("steps", "步骤"), "tiles": t("tiles", "块"),
                "artifacts": t("artifacts", "产物")}.get(str(value.get("unit")), "")
        text += f" · {value.get('completed', 0)}/{value['total']} {unit}"
    return text


class TerminalProgress:
    def __init__(self, renderer: Any) -> None:
        self.renderer = renderer
        self.bar: Any = None
        self.task: Any = None

    def __enter__(self) -> TerminalProgress:
        return self

    def __exit__(self, *exc: Any) -> None:
        if self.bar is not None:
            self.bar.stop()

    def __call__(self, value: dict[str, Any]) -> None:
        if self.renderer.mode == "jsonl":
            from datetime import UTC, datetime

            self.renderer.emit_event({
                "schema_version": 1, "seq": self.renderer.seq + 1, "event": "progress",
                "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "job_id": value.get("job_id"), "data": value,
            })
            return
        if self.renderer.mode != "human" or not self.renderer.stderr.isatty():
            return
        from rich.console import Console
        from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

        if self.bar is None:
            self.bar = Progress(
                SpinnerColumn(), TextColumn("{task.description}"), BarColumn(),
                TimeElapsedColumn(), console=Console(file=self.renderer.stderr), transient=True,
            )
            self.task = self.bar.add_task(label(value), total=value.get("total"))
            self.bar.start()
        task = self.bar.tasks[self.task]
        total = value.get("total")
        if total != task.total or value.get("completed", 0) < task.completed:
            self.bar.reset(self.task, total=total, completed=value.get("completed", 0),
                           description=label(value))
        else:
            self.bar.update(self.task, completed=value.get("completed", 0),
                            description=label(value))
