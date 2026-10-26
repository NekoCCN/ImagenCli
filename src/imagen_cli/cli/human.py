"""Human summaries: rich panels on terminals, structured text elsewhere."""

from __future__ import annotations

import io
import json
import os
import textwrap
from typing import Any

from imagen_cli.i18n import is_chinese, t

__all__ = ["color_enabled", "render"]

_STATE_STYLE = {
    "succeeded": "green",
    "failed": "red",
    "unknown": "yellow",
    "partial_success": "yellow",
    "save_failed": "red",
    "conflict": "magenta",
    "running": "cyan",
    "queued": "blue",
}
_MAX_ROWS = 50


def _columns(columns: int | None) -> int:
    return max(20, columns or 100)


def _rows(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = data.get(key)
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _turn_artifacts(data: dict[str, Any]) -> dict[str, list[str]]:
    """Artifact ids each turn produced, keyed by turn id."""
    produced: dict[str, list[str]] = {}
    for reference in _rows(data, "references"):
        artifact = reference.get("artifact_id")
        if reference.get("role") == "output" and isinstance(artifact, str):
            produced.setdefault(str(reference.get("turn_id")), []).append(artifact)
    return produced


def _unknown_line(turn: dict[str, Any]) -> str:
    job = turn.get("job_id")
    return t(
        f"    outcome unknown — run: imagen job get {job}",
        f"    结果未知 — 运行：imagen job get {job}",
    )


def _schema_rows(data: dict[str, Any]) -> list[tuple[str, str, str]]:
    schema = data.get("input_schema") or {}
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    rows = []
    for name in sorted(properties):
        spec = properties[name] if isinstance(properties[name], dict) else {}
        kind = str(spec.get("type") or ("enum" if "enum" in spec else "any"))
        if "enum" in spec:
            kind = " | ".join(str(item) for item in spec["enum"])
        detail = t("required", "必填") if name in required else t("optional", "可选")
        if "default" in spec:
            detail += t(f" · default {spec['default']}", f" · 默认 {spec['default']}")
        rows.append((name, kind, detail))
    return rows


def _cost_line(cost: dict[str, Any]) -> str:
    amount = cost.get("actual") if cost.get("actual") is not None else cost.get("estimate")
    if amount is None:
        return t("Cost: unknown (not treated as free)", "费用：未知（不视为免费）")
    return t(
        f"Cost: {amount} {cost.get('currency') or ''}".rstrip(),
        f"费用：{amount} {cost.get('currency') or ''}".rstrip(),
    )


def _detail_lines(key: str, entry: dict[str, Any]) -> list[str]:
    """One catalog entry: what it does, what it costs, and how to tune it."""
    nested = entry.get("intro")
    intro: dict[str, Any] = nested if isinstance(nested, dict) else entry
    lines = [f"  ✦ {intro.get('title', key)}"]
    if intro.get("summary"):
        lines.append(t(f"    Operation: {intro['summary']}", f"    操作：{intro['summary']}"))
    if intro.get("benefit"):
        lines.append(
            t(
                f"    Possible advantages: {intro['benefit']}",
                f"    可能的优势：{intro['benefit']}",
            )
        )
    if intro.get("loss"):
        lines.append(
            t(
                f"    Possible drawbacks: {intro['loss']}",
                f"    可能的缺点：{intro['loss']}",
            )
        )
    if intro.get("use_case"):
        lines.append(
            t(
                f"    May be useful for: {intro['use_case']}",
                f"    可能用来：{intro['use_case']}",
            )
        )
    if intro.get("principle"):
        lines.append(t(f"    How: {intro['principle']}", f"    原理：{intro['principle']}"))
    if entry.get("presets"):
        from imagen_cli.application.operator_catalog import display_preset_name

        used_by = "、".join(display_preset_name(str(key)) for key in entry["presets"])
        lines.append(t(f"    Recipes using it: {used_by}", f"    使用它的配方：{used_by}"))
    if entry.get("variants"):
        lines.append(t("    Preset variants:", "    预设变体："))
        for variant in entry["variants"]:
            pairs = ",".join(f"{name}={_shown(value)}" for name, value in variant["params"].items())
            lines.append(f"      {variant['preset']} · {pairs}")
    for step in entry.get("steps") or [entry]:
        lines.extend(_parameter_lines(step))
    if entry.get("example"):
        lines.append(t(f"    Example: {entry['example']}", f"    示例：{entry['example']}"))
    return lines


def _shown(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _parameter_lines(item: dict[str, Any]) -> list[str]:
    """One readable line per tuning knob, so no one needs to read the source."""
    parameters = item.get("parameters") or []
    if not parameters:
        return []
    header = t("    Parameters", "    参数")
    label = item.get("op") or item.get("title")
    if label:
        header += f" · {label}"
    lines = [header + t(":", "：")]
    for parameter in parameters:
        parts = [str(parameter.get("name", "")), str(parameter.get("type", ""))]
        if parameter.get("choices"):
            parts.append("/".join(_shown(choice) for choice in parameter["choices"]))
        elif parameter.get("min") is not None or parameter.get("max") is not None:
            low = _shown(parameter["min"]) if parameter.get("min") is not None else ""
            high = _shown(parameter["max"]) if parameter.get("max") is not None else ""
            parts.append(f"{low}–{high}")
        default = _shown(parameter.get("default"))
        parts.append(t(f"default {default}", f"默认 {default}"))
        if parameter.get("required"):
            parts.append(t("required", "必填"))
        if parameter.get("advanced"):
            parts.append(t("advanced", "高级"))
        if parameter.get("description"):
            parts.append(str(parameter["description"]))
        lines.append("      " + " · ".join(part for part in parts if part))
    return lines


def _plain_lines(value: dict[str, Any]) -> list[str]:
    data = value.get("data") or {}
    lines: list[str] = []
    language = data.get("language")
    if isinstance(language, str):
        suffix = t(" (saved)", "（已保存）") if data.get("saved") else ""
        lines.append(t(f"Language: {language}", f"语言：{language}") + suffix)
    plan = data.get("plan")
    if not isinstance(plan, dict):
        # `asset gc` / `cache purge` use the same key for the dry-run inventory digest.
        plan = {}
    if plan:
        lines.append(
            t(
                f"Plan {plan.get('id')}: {plan.get('operation')} · {plan.get('model')}",
                f"计划 {plan.get('id')}：{plan.get('operation')} · {plan.get('model')}",
            )
        )
        lines.append(t(f"Digest: {plan.get('plan_digest')}", f"摘要：{plan.get('plan_digest')}"))
        if plan.get("id") and plan.get("plan_digest"):
            lines.append(
                t("Next: ", "下一步：")
                + f"imagen plan approve {plan['id']} --digest {plan['plan_digest']}"
            )
    job = data.get("job") or {}
    if job:
        lines.append(
            t(
                f"Job {job.get('id')}: {job.get('state')}"
                f" (remote {job.get('remote_state')}, local {job.get('local_save_state')})",
                f"任务 {job.get('id')}：{job.get('state')}"
                f"（远端 {job.get('remote_state')}，本地 {job.get('local_save_state')}）",
            )
        )
        lines.append(_cost_line(job.get("cost") or {}))
    profile = data.get("profile") or {}
    if profile and profile.get("id"):
        lines.append(
            f"Profile {profile.get('id')} · {profile.get('provider')}/{profile.get('family')}"
            f" · {profile.get('model_default')} · revision {profile.get('revision')}"
        )
    session = data.get("session") or {}
    if session and session.get("name"):
        lines.append(
            t(
                f"Session {session.get('name')} ({session.get('id')})"
                f" · version {session.get('version')}",
                f"会话 {session.get('name')}（{session.get('id')}）"
                f"· version {session.get('version')}",
            )
        )
        if session.get("intent"):
            lines.append(t(f"Intent: {session.get('intent')}", f"意图：{session.get('intent')}"))
    for row in _rows(data, "sessions")[:_MAX_ROWS]:
        lines.append(
            t(
                f"Session {row.get('name')} · version {row.get('version')} · {row.get('id')}",
                f"会话 {row.get('name')} · version {row.get('version')} · {row.get('id')}",
            )
        )
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
        lines.append(
            t(f"Model {ident}", f"模型 {ident}")
            + f"{scale}{(' · ' + str(detail)) if detail else ''}"
        )
    if len(models) > _MAX_ROWS:
        lines.append(
            t(
                f"…{len(models) - _MAX_ROWS} more models; use --output json to see all",
                f"…还有 {len(models) - _MAX_ROWS} 个模型；--output json 查看全部",
            )
        )
    entries = _rows(data, "entries")
    if entries:
        total = data.get("planned_bytes", sum(item.get("bytes", 0) for item in entries))
        lines.append(
            t(
                f"{len(entries)} reclaimable items, {total} bytes in total",
                f"可回收 {len(entries)} 项，共 {total} 字节",
            )
        )
        for entry in entries[:_MAX_ROWS]:
            lines.append(
                t(
                    f"  {entry.get('kind')} · {entry.get('id')} · {entry.get('bytes')} bytes",
                    f"  {entry.get('kind')} · {entry.get('id')} · {entry.get('bytes')} 字节",
                )
            )
    commands = _rows(data, "commands")
    for row in commands:
        state = t("available", "可用") if row.get("available") else t("not delivered", "未交付")
        lines.append(f"{row.get('name')} · {state} · {row.get('summary')}")
    parameters = _schema_rows(data)
    for name, kind, detail in parameters:
        lines.append(
            t(f"  parameter {name} · {kind} · {detail}", f"  参数 {name} · {kind} · {detail}")
        )
    separator = "、" if is_chinese() else ", "
    details = data.get("details")
    # A detail request is about one entry; the full identifier list would bury it.
    if not details:
        operations = data.get("operations")
        if isinstance(operations, list) and operations:
            lines.append(
                t("Operations: ", "操作：") + separator.join(str(item) for item in operations)
            )
        presets = data.get("presets")
        if isinstance(presets, list) and presets:
            from imagen_cli.application.operator_catalog import display_preset_name

            lines.append(
                t("Presets: ", "预设：")
                + separator.join(display_preset_name(str(item)) for item in presets)
            )
    if isinstance(details, dict) and details:
        lines.append(t("Catalog and rationale:", "知识库与原理说明："))
        for key, entry in details.items():
            lines.extend(
                _detail_lines(key, entry) if isinstance(entry, dict) else [f"  ✦ {key}: {entry}"]
            )
    if data.get("removed") and data.get("artifact_id"):
        purged_note = t(" (file reclaimed)", "（已回收文件）") if data.get("purged") else ""
        sess_lbl = data.get("session_id")
        art_lbl = data.get("artifact_id")
        lines.append(
            t(
                f"✓ Removed artifact {art_lbl} from session {sess_lbl}{purged_note}",
                f"✓ 已从会话 {sess_lbl} 移除产物 {art_lbl}{purged_note}",
            )
        )
    manual = data.get("manual")
    if isinstance(manual, str) and manual:
        lines.append(manual)
    turns = _rows(data, "turns")
    if turns:
        produced = _turn_artifacts(data)
        lines.append(t(f"Turns: {len(turns)}", f"轮次 {len(turns)}"))
        for turn in turns[:_MAX_ROWS]:
            line = f"  {turn.get('id')} · {turn.get('operation')} · {turn.get('creation_state')}"
            produced_ids = produced.get(str(turn.get("id")))
            if produced_ids:
                line += f" · {', '.join(produced_ids)}"
            lines.append(line)
            if turn.get("creation_state") == "unknown":
                lines.append(_unknown_line(turn))
    artifacts = _rows(data, "artifacts")
    for artifact in artifacts[:_MAX_ROWS]:
        if artifact.get("path"):
            # Paths are never truncated: terminal soft-wrap keeps them copyable in full.
            lines.append(t("File: ", "文件：") + artifact["path"])
    summary = data.get("summary") or {}
    if summary:
        lines.append(json.dumps(summary, ensure_ascii=False))
    if not lines:
        payload = json.dumps(data, ensure_ascii=False)
        lines.append(payload)
    return lines


def _error_lines(value: dict[str, Any]) -> list[str]:
    error = value.get("error") or {}
    message = error.get("message") or t("The command did not complete.", "命令未成功完成。")
    lines = [t(f"Failed: {message}", f"失败：{message}")]
    for action in error.get("next_actions") or []:
        command = action.get("command") if isinstance(action, dict) else None
        if command:
            lines.append(t(f"Next: {command}", f"下一步：{command}"))
    status = value.get("status", "unknown")
    return lines + [t(f"status: {status}", f"状态：{status}")]


def _plain(value: dict[str, Any], width: int) -> list[str]:
    if not value.get("ok"):
        wrapped: list[str] = []
        for line in _error_lines(value):
            wrapped.extend(
                textwrap.wrap(line, width, break_long_words=False, break_on_hyphens=False)
                or [line[:width]]
            )
        return wrapped
    lines = _plain_lines(value)
    file_prefix = t("File: ", "文件：")
    wrapped = []
    for line in lines:
        if line.startswith(file_prefix) or len(line) <= width:
            wrapped.append(line)
            continue
        for piece in textwrap.wrap(
            line, width, subsequent_indent=" ", break_long_words=False, break_on_hyphens=False
        ):
            # A token wider than the terminal (ids, URLs) still has to fit.
            wrapped.append(piece if len(piece) <= width else piece[: width - 1] + "…")
    return wrapped


def _rich(value: dict[str, Any], width: int) -> str:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    buffer = io.StringIO()
    console = Console(
        file=buffer, width=width, force_terminal=True, color_system="standard", highlight=False
    )
    if not value.get("ok"):
        body = "\n".join(
            [
                f"[bold]{line}[/bold]" if index == 0 else f"[cyan]{line}[/cyan]"
                for index, line in enumerate(_error_lines(value)[:-1])
            ]
        )
        console.print(Panel(body, title=t("Failed", "失败"), border_style="red"))
        return buffer.getvalue()
    data = value.get("data") or {}
    language = data.get("language")
    if isinstance(language, str):
        suffix = t(" (saved)", "（已保存）") if data.get("saved") else ""
        console.print(
            t(f"Language: [bold]{language}[/bold]", f"语言：[bold]{language}[/bold]") + suffix
        )
    plan = data.get("plan")
    if not isinstance(plan, dict):
        # `asset gc` / `cache purge` use the same key for the dry-run inventory digest.
        plan = {}
    if plan:
        lines = [
            f"[bold]{plan.get('id')}[/bold]",
            t(
                f"Operation {plan.get('operation')} · Model {plan.get('model')}",
                f"操作 {plan.get('operation')} · 模型 {plan.get('model')}",
            ),
            t(f"Digest {plan.get('plan_digest')}", f"摘要 {plan.get('plan_digest')}"),
        ]
        if plan.get("id") and plan.get("plan_digest"):
            lines.append(
                f"[cyan]imagen plan approve {plan['id']} --digest {plan['plan_digest']}[/cyan]"
            )
        console.print(Panel("\n".join(lines), title=t("Plan", "计划"), border_style="blue"))
    job = data.get("job") or {}
    if job:
        style = _STATE_STYLE.get(str(job.get("state")), "white")
        console.print(
            Panel(
                f"[bold]{job.get('id')}[/bold]\n"
                + t("State ", "状态 ")
                + f"[{style}]{job.get('state')}[/{style}] · "
                + t(
                    f"remote {job.get('remote_state')} · local {job.get('local_save_state')}",
                    f"远端 {job.get('remote_state')} · 本地 {job.get('local_save_state')}",
                ),
                title=t("Job", "任务"),
                border_style=style,
            )
        )
        cost = job.get("cost") or {}
        amount = cost.get("actual") if cost.get("actual") is not None else cost.get("estimate")
        unknown_cost = t("Cost: unknown (not treated as free)", "费用：未知（不视为免费）")
        console.print(
            f"[yellow]{unknown_cost}[/yellow]"
            if amount is None
            else t(
                f"Cost: [bold]{amount}[/bold] {cost.get('currency') or ''}",
                f"费用：[bold]{amount}[/bold] {cost.get('currency') or ''}",
            )
        )
    profile = data.get("profile") or {}
    if profile and profile.get("id"):
        console.print(
            Panel(
                f"[bold]{profile.get('id')}[/bold] · "
                f"{profile.get('provider')}/{profile.get('family')}\n"
                f"{profile.get('model_default')} · revision {profile.get('revision')}",
                title="Profile",
                border_style="green",
            )
        )
    session = data.get("session") or {}
    if session and session.get("name"):
        intent = ""
        if session.get("intent"):
            intent = f"\n{t('Intent', '意图')}: {session.get('intent')}"
        console.print(
            Panel(
                f"[bold]{session.get('name')}[/bold] · version {session.get('version')}\n"
                f"{session.get('id')}{intent}",
                title=t("Session", "会话"),
                border_style="green",
            )
        )
    sessions = _rows(data, "sessions")
    if sessions:
        table = Table(title=t("Sessions", "会话"), header_style="bold")
        for column in (t("Name", "名称"), t("Version", "版本"), "ID"):
            table.add_column(column, overflow="fold")
        for row in sessions[:_MAX_ROWS]:
            table.add_row(str(row.get("name")), str(row.get("version")), str(row.get("id")))
        console.print(table)
    profiles = _rows(data, "profiles")
    if profiles:
        table = Table(title="Providers", header_style="bold")
        for column in ("ID", "Provider", "Family", t("Model", "模型"), "Revision"):
            table.add_column(column, overflow="fold")
        for row in profiles[:_MAX_ROWS]:
            table.add_row(
                str(row.get("id")),
                str(row.get("provider")),
                str(row.get("family")),
                str(row.get("model_default")),
                str(row.get("revision")),
            )
        console.print(table)
    models = _rows(data, "models") or _rows(data, "provider_models")
    if models:
        table = Table(title=t("Models", "模型"), header_style="bold")
        for column in (t("Model", "模型"), t("Scale", "倍率"), t("Evidence/License", "证据/许可")):
            table.add_column(column, overflow="fold")
        for row in models[:_MAX_ROWS]:
            table.add_row(
                str(row.get("model_id") or row.get("model")),
                f"x{row['scale']}" if row.get("scale") else "-",
                str(row.get("evidence") or row.get("license") or ""),
            )
        console.print(table)
    entries = _rows(data, "entries")
    if entries:
        console.print(
            t(
                f"Reclaimable [bold]{len(entries)}[/bold] items, "
                f"[bold]{data.get('planned_bytes', 0)}[/bold] bytes in total",
                f"可回收 [bold]{len(entries)}[/bold] 项，共 "
                f"[bold]{data.get('planned_bytes', 0)}[/bold] 字节",
            )
        )
        table = Table(header_style="bold")
        for column in (t("Kind", "类型"), t("Entry", "条目"), t("Bytes", "字节")):
            table.add_column(column, overflow="fold")
        for entry in entries[:_MAX_ROWS]:
            table.add_row(str(entry.get("kind")), str(entry.get("id")), str(entry.get("bytes")))
        console.print(table)
    parameters = _schema_rows(data)
    if parameters:
        table = Table(title=t("Parameters", "参数"), header_style="bold")
        for column in (t("Name", "名称"), t("Type", "类型"), t("Detail", "说明")):
            table.add_column(column, overflow="fold")
        for name, kind, detail in parameters:
            table.add_row(name, kind, detail)
        console.print(table)
    commands = _rows(data, "commands")
    if commands:
        table = Table(title=t("Commands", "命令"), header_style="bold")
        for column in (t("Command", "命令"), t("State", "状态"), t("Summary", "说明")):
            table.add_column(column, overflow="fold")
        for row in commands:
            table.add_row(
                str(row.get("name")),
                t("available", "可用") if row.get("available") else t("not delivered", "未交付"),
                str(row.get("summary")),
            )
        console.print(table)
    operations = data.get("operations")
    details = data.get("details")
    if isinstance(operations, list) and operations and not details:
        console.print(
            Panel(
                ("、" if is_chinese() else ", ").join(str(item) for item in operations),
                title=t("Operations", "操作"),
            )
        )
    if isinstance(details, dict) and details:
        from rich.text import Text

        detail_body: list[str] = []
        for key, entry in details.items():
            detail_body.extend(
                _detail_lines(key, entry) if isinstance(entry, dict) else [f"  ✦ {key}: {entry}"]
            )
        console.print(
            Panel(Text("\n".join(detail_body)), title=t("Catalog and rationale", "知识库与原理"))
        )
    manual = data.get("manual")
    if isinstance(manual, str) and manual:
        console.print(Panel(manual, title=t("Calling rules", "调用规则")))
    turns = _rows(data, "turns")
    if turns:
        produced = _turn_artifacts(data)
        table = Table(title=t("Turns", "轮次"), header_style="bold")
        for column in ("ID", t("Operation", "操作"), t("State", "状态")):
            table.add_column(column, overflow="fold")
        for turn in turns[:_MAX_ROWS]:
            table.add_row(
                str(turn.get("id")),
                str(turn.get("operation")),
                str(turn.get("creation_state")),
            )
        console.print(table)
        for turn in turns[:_MAX_ROWS]:
            produced_ids = produced.get(str(turn.get("id")))
            if produced_ids:
                console.print(f"{turn.get('id')} → {', '.join(produced_ids)}")
            if turn.get("creation_state") == "unknown":
                console.print(_unknown_line(turn).strip(), style="yellow")
    artifacts = [item for item in _rows(data, "artifacts") if item.get("path")]
    if artifacts:
        table = Table(title=t("Artifacts", "产物"), header_style="bold")
        table.add_column("#" if not is_chinese() else "序号", justify="right")
        table.add_column(t("File", "文件"), overflow="fold")
        for index, artifact in enumerate(artifacts, start=1):
            table.add_row(str(index), str(artifact["path"]))
        console.print(table)
    if not any(
        (
            language,
            plan,
            job,
            profile,
            session,
            sessions,
            profiles,
            models,
            entries,
            commands,
            operations,
            turns,
            artifacts,
            parameters,
            details,
        )
    ):
        console.print(json.dumps(data, ensure_ascii=False))
    return buffer.getvalue()


def render(value: dict[str, Any], *, columns: int | None = None, color: bool = False) -> str:
    width = _columns(columns)
    data = value.get("data") or {}
    if value.get("ok") and len(data) == 1 and isinstance(data.get("version"), str):
        return f"imagen {data['version']}"
    if value.get("ok") and data.get("presets") and not data.get("details"):
        from imagen_cli.application.operator_catalog import browse_entries

        lines = [
            t(
                "Postprocessing catalog (experimental; call identifiers remain compatible)",
                "后处理目录（实验性 experimental；调用标识保留兼容）",
            )
        ]
        entries = browse_entries()
        for group in dict.fromkeys(entry["group"] for entry in entries):
            lines.append(f"\n{group}")
            for entry in entries:
                if entry["group"] == group:
                    key = entry["key"].split(" (", 1)[0]
                    lines.extend((f"  {entry['name']}", f'    --preset "{key}"'))
        lines.append(t("\nOperations (--op):", "\n底层算子（--op）："))
        shown = {entry["key"] for entry in entries}
        lines.extend(f"  {op}" for op in data.get("operations", []) if op not in shown)
        lines.append(
            t(
                "\nInspect one: imagen ops --op NAME"
                "  ·  apply: imagen transform ART --op 'NAME:key=value'",
                "\n查看参数：imagen ops --op 名称"
                "  ·  应用：imagen transform ART --op '名称:参数=值'",
            )
        )
        return "\n".join(lines)
    text = _rich(value, width) if color else "\n".join(_plain(value, width))
    return text.rstrip("\n")


def color_enabled(stream: Any) -> bool:
    return bool(
        getattr(stream, "isatty", lambda: False)()
        and os.environ.get("NO_COLOR") is None
        and os.environ.get("TERM") not in {None, "", "dumb"}
    )
