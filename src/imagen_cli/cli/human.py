"""Human summaries: rich panels on terminals, structured text elsewhere."""

from __future__ import annotations

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
_MAX_ROWS = 50


def _columns(columns: int | None) -> int:
    return max(20, columns or 100)


def _rows(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = data.get(key)
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _cost_line(cost: dict[str, Any]) -> str:
    amount = cost.get("actual") if cost.get("actual") is not None else cost.get("estimate")
    if amount is None:
        return "费用：未知（不视为免费）"
    return f"费用：{amount} {cost.get('currency') or ''}".rstrip()


def _plain_lines(value: dict[str, Any]) -> list[str]:
    data = value.get("data") or {}
    lines: list[str] = []
    plan = data.get("plan") or {}
    if plan:
        lines.append(f"计划 {plan.get('id')}：{plan.get('operation')} · {plan.get('model')}")
        lines.append(f"摘要：{plan.get('plan_digest')}")
        if plan.get("id") and plan.get("plan_digest"):
            lines.append(
                f"下一步：imagen plan approve {plan['id']} --digest {plan['plan_digest']}"
            )
    job = data.get("job") or {}
    if job:
        lines.append(
            f"任务 {job.get('id')}：{job.get('state')}"
            f"（远端 {job.get('remote_state')}，本地 {job.get('local_save_state')}）"
        )
        lines.append(_cost_line(job.get("cost") or {}))
    session = data.get("session") or {}
    if session and session.get("name"):
        lines.append(
            f"会话 {session.get('name')}（{session.get('id')}）· version {session.get('version')}"
        )
    for row in _rows(data, "sessions")[:_MAX_ROWS]:
        lines.append(f"会话 {row.get('name')} · version {row.get('version')} · {row.get('id')}")
    for row in _rows(data, "profiles")[:_MAX_ROWS]:
        lines.append(
            f"Provider {row.get('id')} · {row.get('provider')}/{row.get('family')}"
            f" · {row.get('model_default')} · revision {row.get('revision')}"
        )
    models = _rows(data, "models") or _rows(data, "provider_models")
    for row in models[:_MAX_ROWS]:
        ident = row.get("model_id") or row.get("model")
        detail = row.get("evidence") or row.get("license") or ""
        scale = f" · x{row['scale']}" if row.get("scale") else ""
        lines.append(f"模型 {ident}{scale}{(' · ' + str(detail)) if detail else ''}")
    capabilities = _rows(data, "capabilities")
    for row in capabilities[:_MAX_ROWS]:
        lines.append(
            f"能力 {row.get('provider')}/{row.get('model')} · {row.get('operation')}"
            f" · {row.get('support_status')}"
        )
    entries = _rows(data, "entries")
    if entries:
        total = data.get("planned_bytes", sum(item.get("bytes", 0) for item in entries))
        lines.append(f"可回收 {len(entries)} 项，共 {total} 字节")
        for entry in entries[:_MAX_ROWS]:
            lines.append(
                f"  {entry.get('kind')} · {entry.get('id')} · {entry.get('bytes')} 字节"
            )
    commands = _rows(data, "commands")
    for row in commands:
        state = "可用" if row.get("available") else "未交付"
        lines.append(f"{row.get('name')} · {state} · {row.get('summary')}")
    operations = data.get("operations")
    if isinstance(operations, list) and operations:
        lines.append("操作：" + "、".join(str(item) for item in operations))
    manual = data.get("manual")
    if isinstance(manual, str) and manual:
        lines.append(manual)
    turns = _rows(data, "turns")
    if turns:
        lines.append(f"轮次 {len(turns)}")
        for turn in turns[:_MAX_ROWS]:
            lines.append(
                f"  {turn.get('id')} · {turn.get('operation')} · {turn.get('creation_state')}"
            )
    artifacts = _rows(data, "artifacts")
    for artifact in artifacts[:_MAX_ROWS]:
        if artifact.get("path"):
            # Paths are never truncated: terminal soft-wrap keeps them copyable in full.
            lines.append(f"文件：{artifact['path']}")
    summary = data.get("summary") or {}
    if summary:
        lines.append(json.dumps(summary, ensure_ascii=False))
    if not lines:
        payload = json.dumps(data, ensure_ascii=False)
        lines.append(payload)
    return lines


def _plain(value: dict[str, Any], width: int) -> list[str]:
    if not value.get("ok"):
        error = value.get("error") or {}
        message = error.get("message") or "命令未成功完成。"
        return [f"失败：{message}（状态：{value.get('status', 'unknown')}）"[:width]]
    lines = _plain_lines(value)
    return [
        line if line.startswith("文件：") or len(line) <= width else line[: width - 1] + "…"
        for line in lines
    ]


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
    plan = data.get("plan") or {}
    if plan:
        lines = [
            f"[bold]{plan.get('id')}[/bold]",
            f"操作 {plan.get('operation')} · 模型 {plan.get('model')}",
            f"摘要 {plan.get('plan_digest')}",
        ]
        if plan.get("id") and plan.get("plan_digest"):
            lines.append(
                f"[cyan]imagen plan approve {plan['id']} --digest {plan['plan_digest']}[/cyan]"
            )
        console.print(Panel("\n".join(lines), title="计划", border_style="blue"))
    job = data.get("job") or {}
    if job:
        style = _STATE_STYLE.get(str(job.get("state")), "white")
        console.print(Panel(
            f"[bold]{job.get('id')}[/bold]\n"
            f"状态 [{style}]{job.get('state')}[/{style}] · "
            f"远端 {job.get('remote_state')} · 本地 {job.get('local_save_state')}",
            title="任务", border_style=style,
        ))
        console.print(_cost_line(job.get("cost") or {}))
    session = data.get("session") or {}
    if session and session.get("name"):
        console.print(Panel(
            f"[bold]{session.get('name')}[/bold] · version {session.get('version')}\n"
            f"{session.get('id')}",
            title="会话", border_style="green",
        ))
    sessions = _rows(data, "sessions")
    if sessions:
        table = Table(title="会话", header_style="bold")
        for column in ("名称", "版本", "ID"):
            table.add_column(column, overflow="fold")
        for row in sessions[:_MAX_ROWS]:
            table.add_row(str(row.get("name")), str(row.get("version")), str(row.get("id")))
        console.print(table)
    profiles = _rows(data, "profiles")
    if profiles:
        table = Table(title="Providers", header_style="bold")
        for column in ("ID", "Provider", "Family", "模型", "Revision"):
            table.add_column(column, overflow="fold")
        for row in profiles[:_MAX_ROWS]:
            table.add_row(
                str(row.get("id")), str(row.get("provider")), str(row.get("family")),
                str(row.get("model_default")), str(row.get("revision")),
            )
        console.print(table)
    models = _rows(data, "models") or _rows(data, "provider_models")
    if models:
        table = Table(title="模型", header_style="bold")
        for column in ("模型", "倍率", "证据/许可"):
            table.add_column(column, overflow="fold")
        for row in models[:_MAX_ROWS]:
            table.add_row(
                str(row.get("model_id") or row.get("model")),
                f"x{row['scale']}" if row.get("scale") else "-",
                str(row.get("evidence") or row.get("license") or ""),
            )
        console.print(table)
    capabilities = _rows(data, "capabilities")
    if capabilities:
        table = Table(title="能力", header_style="bold")
        for column in ("Provider", "模型", "操作", "状态"):
            table.add_column(column, overflow="fold")
        for row in capabilities[:_MAX_ROWS]:
            table.add_row(
                str(row.get("provider")), str(row.get("model")),
                str(row.get("operation")), str(row.get("support_status")),
            )
        console.print(table)
    entries = _rows(data, "entries")
    if entries:
        console.print(
            f"可回收 [bold]{len(entries)}[/bold] 项，共 "
            f"[bold]{data.get('planned_bytes', 0)}[/bold] 字节"
        )
        table = Table(header_style="bold")
        for column in ("类型", "条目", "字节"):
            table.add_column(column, overflow="fold")
        for entry in entries[:_MAX_ROWS]:
            table.add_row(str(entry.get("kind")), str(entry.get("id")), str(entry.get("bytes")))
        console.print(table)
    commands = _rows(data, "commands")
    if commands:
        table = Table(title="命令", header_style="bold")
        for column in ("命令", "状态", "说明"):
            table.add_column(column, overflow="fold")
        for row in commands:
            table.add_row(
                str(row.get("name")),
                "可用" if row.get("available") else "未交付",
                str(row.get("summary")),
            )
        console.print(table)
    operations = data.get("operations")
    if isinstance(operations, list) and operations:
        console.print(Panel("、".join(str(item) for item in operations), title="操作"))
    manual = data.get("manual")
    if isinstance(manual, str) and manual:
        console.print(Panel(manual, title="调用规则"))
    turns = _rows(data, "turns")
    if turns:
        table = Table(title="轮次", header_style="bold")
        for column in ("ID", "操作", "状态"):
            table.add_column(column, overflow="fold")
        for turn in turns[:_MAX_ROWS]:
            table.add_row(
                str(turn.get("id")), str(turn.get("operation")),
                str(turn.get("creation_state")),
            )
        console.print(table)
    artifacts = [item for item in _rows(data, "artifacts") if item.get("path")]
    if artifacts:
        table = Table(title="产物", header_style="bold")
        table.add_column("序号", justify="right")
        table.add_column("文件", overflow="fold")
        for index, artifact in enumerate(artifacts, start=1):
            table.add_row(str(index), str(artifact["path"]))
        console.print(table)
    if not any((plan, job, session, sessions, profiles, models, capabilities, entries,
                commands, operations, turns, artifacts)):
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
