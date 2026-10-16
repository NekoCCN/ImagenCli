"""Human summaries: rich panels on capable terminals, plain text everywhere else."""

import io
import json
import os
from typing import Any

__all__ = ["color_enabled", "render"]

_STATE_STYLE = {
    "succeeded": "green", "failed": "red", "unknown": "yellow",
    "partial_success": "yellow", "save_failed": "red", "conflict": "magenta",
    "running": "cyan", "queued": "blue",
}


def _columns(columns: int | None) -> int:
    return max(20, columns or 100)


def _plain(value: dict[str, Any], width: int) -> list[str]:
    if not value.get("ok"):
        error = value.get("error") or {}
        message = error.get("message") or "命令未成功完成。"
        return [f"失败：{message}（状态：{value.get('status', 'unknown')}）".ljust(0)[:width]]
    data = value.get("data") or {}
    lines: list[str] = []
    job = data.get("job") or {}
    if job:
        lines.append(
            f"任务 {job.get('id')}：{job.get('state')}"
            f"（远端 {job.get('remote_state')}，本地 {job.get('local_save_state')}）"
        )
        cost = job.get("cost") or {}
        amount = cost.get("actual") if cost.get("actual") is not None else cost.get("estimate")
        lines.append(
            "费用：未知（不视为免费）" if amount is None
            else f"费用：{amount} {cost.get('currency') or ''}".rstrip()
        )
    paths = [f"文件：{artifact['path']}" for artifact in data.get("artifacts") or []
             if artifact.get("path")]
    if not lines and not paths:
        lines.append(json.dumps(data, ensure_ascii=False))
    # Paths are never truncated: terminal soft-wrap keeps them copyable in full.
    return [line if len(line) <= width else line[: width - 1] + "…" for line in lines] + paths


def _rich(value: dict[str, Any], width: int) -> str:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    buffer = io.StringIO()
    console = Console(file=buffer, width=width, force_terminal=True, highlight=False)
    if not value.get("ok"):
        message = (value.get("error") or {}).get("message") or "命令未成功完成。"
        console.print(Panel(message, title="失败", border_style="red"))
        return buffer.getvalue()
    data = value.get("data") or {}
    job = data.get("job") or {}
    if job:
        style = _STATE_STYLE.get(str(job.get("state")), "white")
        console.print(Panel(
            f"[bold]{job.get('id')}[/bold]\n"
            f"状态 [{style}]{job.get('state')}[/{style}] · "
            f"远端 {job.get('remote_state')} · 本地 {job.get('local_save_state')}",
            title="任务", border_style=style,
        ))
        cost = job.get("cost") or {}
        amount = cost.get("actual") if cost.get("actual") is not None else cost.get("estimate")
        console.print(
            "[yellow]费用：未知（不视为免费）[/yellow]" if amount is None
            else f"费用：[bold]{amount}[/bold] {cost.get('currency') or ''}"
        )
    artifacts = [item for item in data.get("artifacts") or [] if item.get("path")]
    if artifacts:
        table = Table(title="产物", show_header=True, header_style="bold")
        table.add_column("序号", justify="right")
        table.add_column("文件", overflow="fold")
        for index, artifact in enumerate(artifacts, start=1):
            table.add_row(str(index), str(artifact["path"]))
        console.print(table)
    if not job and not artifacts:
        console.print(json.dumps(data, ensure_ascii=False))
    return buffer.getvalue()


def render(value: dict[str, Any], *, columns: int | None = None, color: bool = False) -> str:
    width = _columns(columns)
    text = _rich(value, width) if color else "\n".join(_plain(value, width))
    return text.rstrip("\n")


def color_enabled(stream: Any) -> bool:
    return bool(
        getattr(stream, "isatty", lambda: False)()
        and os.environ.get("NO_COLOR") is None
        and os.environ.get("TERM") not in {None, "", "dumb"}
    )
