"""Small terminal selector shared by explicit setup commands."""

from __future__ import annotations

import os
import sys
from typing import TextIO

from rich.console import Console
from rich.live import Live
from rich.text import Text

from imagen_cli.i18n import t


class Back(Exception):
    """Return to the previous step of an interactive setup."""


# Which keys do what; `apply` is pure so the state machine is testable without a TTY.
_UP = {"\x1b[A", "\x1bOA", "\x00H", "\xe0H", "k"}
_DOWN = {"\x1b[B", "\x1bOB", "\x00P", "\xe0P", "j"}
_CONFIRM = {"\r", "\n"}
_CANCEL = {"\x1b", "q"}


def apply(
    labels: list[str], picked: list[str], cursor: int, key: str
) -> tuple[list[str], int, bool | None]:
    """One keystroke of the checklist: (selection, cursor, done)."""
    if key in _UP:
        return picked, (cursor - 1) % len(labels), None
    if key in _DOWN:
        return picked, (cursor + 1) % len(labels), None
    if key == " ":
        name = labels[cursor]
        if name in picked:
            return [item for item in picked if item != name], cursor, None
        return [*picked, name], cursor, None
    if key == "a":
        return ([] if len(picked) == len(labels) else list(labels)), cursor, None
    if key in _CANCEL:
        return [], cursor, False
    if key in _CONFIRM:
        return (picked or [labels[cursor]]), cursor, True
    return picked, cursor, None


def _tty(stream: TextIO) -> bool:
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False


def _read_key(stream: TextIO) -> str:
    if sys.platform == "win32":
        import msvcrt

        getwch = msvcrt.getwch
        char: str = getwch()
        return char + (getwch() if char in "\x00\xe0" else "")
    else:
        import select
        import termios
        import tty

        descriptor = stream.fileno()
        settings = termios.tcgetattr(descriptor)
        try:
            tty.setcbreak(descriptor, termios.TCSADRAIN)  # TCSAFLUSH drops queued keys
            # Read the fd, not the text wrapper: its buffer would hide the escape tail.
            char = os.read(descriptor, 1).decode("utf-8", "replace")
            if char == "\x1b":
                for _ in range(2):
                    if not select.select([descriptor], [], [], 0.05)[0]:
                        break
                    char += os.read(descriptor, 1).decode("utf-8", "replace")
        finally:
            termios.tcsetattr(descriptor, termios.TCSADRAIN, settings)
        return char


def _render(
    labels: list[str], picked: list[str], cursor: int, height: int, allow_back: bool = False
) -> Text:
    start = max(0, min(cursor - height // 2, len(labels) - height))
    body = Text()
    for index in range(start, min(start + height, len(labels))):
        name = labels[index]
        body.append("› " if index == cursor else "  ", style="bold cyan")
        body.append("◉ " if name in picked else "○ ", style="green" if name in picked else "dim")
        body.append(name, style="bold" if index == cursor else "")
        body.append("\n")
    if start or start + height < len(labels):
        end = min(start + height, len(labels))
        body.append(f"  {start + 1}-{end}/{len(labels)}\n", style="dim")
    body.append(
        t(
            "↑/↓ move · space select · a all · enter confirm · q cancel",
            "↑/↓ 移动 · 空格选择 · a 全选 · 回车确认 · q 取消",
        ),
        style="dim",
    )
    if allow_back:
        body.append(t(" · b back", " · b 返回"), style="dim")
        if not picked:
            body.append(t(" · select at least one", " · 至少选择一项"), style="yellow")
    return body


def _pick(
    numbers: list[str], stdin: TextIO, stderr: TextIO, selected: list[str],
    allow_back: bool = False,
) -> list[str]:
    for index, label in enumerate(numbers, 1):
        mark = "*" if label in selected else " "
        stderr.write(f"{index:2}. [{mark}] {label}\n")
    while True:
        stderr.write(t(
            "Enter numbers (comma-separated, blank keeps the starred ones): ",
            "输入序号（多个用逗号分隔，空白保留带 * 的项）：",
        ))
        if allow_back:
            stderr.write(t("[a all, b back, q cancel] ", "[a 全选，b 返回，q 取消] "))
        stderr.flush()
        raw = stdin.readline()
        if raw == "" or raw.strip().lower() == "q":
            return []
        value = raw.strip()
        if allow_back and value.lower() == "b":
            raise Back
        if allow_back and value.lower() == "a":
            return list(numbers)
        chosen = list(selected) if not value else []
        if value:
            try:
                indices = list(dict.fromkeys(int(item.strip()) - 1 for item in value.split(",")))
                if all(0 <= index < len(numbers) for index in indices):
                    chosen = [numbers[index] for index in indices]
            except ValueError:
                pass
        if chosen or not allow_back:
            return chosen
        stderr.write(t("Select at least one valid number.\n", "请至少选择一个有效序号。\n"))


def choose(
    labels: list[str], *, stdin: TextIO, stderr: TextIO, selected: list[str] | None = None,
    allow_back: bool = False,
) -> list[str]:
    """Tick agents in a checklist; `selected` starts ticked."""
    picked = [label for label in labels if label in (selected or [])]
    if not _tty(stdin) or not _tty(stderr):
        return _pick(labels, stdin, stderr, picked, allow_back)
    console = Console(file=stderr, force_terminal=True, highlight=False)
    height = max(5, min(len(labels), console.height - 3))
    cursor = 0
    with Live(
        _render(labels, picked, cursor, height, allow_back), console=console, transient=True
    ) as live:
        while True:
            key = _read_key(stdin)
            if not key:
                return []
            if allow_back and key == "b":
                raise Back
            if allow_back and key in _CONFIRM and not picked:
                continue
            picked, cursor, done = apply(labels, picked, cursor, key)
            if done is not None:
                return picked if done else []
            live.update(_render(labels, picked, cursor, height, allow_back))
