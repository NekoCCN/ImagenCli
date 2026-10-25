"""Small terminal selector shared by explicit setup commands."""

from typing import TextIO

from imagen_cli.i18n import t


def choose(labels: list[str], *, stdin: TextIO, stderr: TextIO) -> list[str]:
    for index, label in enumerate(labels, 1):
        stderr.write(f"{index:2}. {label}\n")
    stderr.write(
        t(
            "Enter numbers (comma-separated, blank to cancel): ",
            "输入序号（多个用逗号分隔，空白取消）：",
        )
    )
    stderr.flush()
    value = stdin.readline().strip()
    if not value:
        return []
    try:
        indices = list(dict.fromkeys(int(item.strip()) - 1 for item in value.split(",")))
        if any(index < 0 or index >= len(labels) for index in indices):
            return []
        return [labels[index] for index in indices]
    except ValueError:
        return []
