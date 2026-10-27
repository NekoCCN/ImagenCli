"""Image preview helpers; ANSI half-blocks with a text fallback."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, cast

from PIL import Image, ImageOps

from imagen_cli.i18n import t

RESET = "\x1b[0m"


def supported(stream: Any = None) -> bool:
    stream = stream or sys.stdout
    if not getattr(stream, "isatty", lambda: False)():
        return False
    term = os.environ.get("TERM", "")
    if not term or term == "dumb" or os.environ.get("NO_COLOR"):
        return False
    return os.environ.get("COLORTERM", "").lower() in {"truecolor", "24bit"}


def describe(row: dict[str, Any], path: Path | None) -> str:
    size = f"{row.get('width')}x{row.get('height')}"
    if path is None:
        return f"{row['id']}\n{size}\n" + t("(file unavailable)", "(文件不可用)")
    return f"{path}\n{size}\n{row['sha256'][:16]}…"


def ansi_blocks(path: Path, *, cols: int = 40, rows: int = 20) -> str:
    """Two vertical pixels per character cell, using the upper half block."""
    with Image.open(path) as image:
        return ansi_image(image, cols=cols, rows=rows)


def ansi_image(image: Image.Image, *, cols: int = 40, rows: int = 20) -> str:
    """Render a contained true-color preview without stretching the source."""
    source = image.convert("RGBA")
    scaled = ImageOps.contain(
        source, (max(1, cols), max(2, rows * 2)), Image.Resampling.LANCZOS
    )
    canvas = Image.new("RGBA", (scaled.width, scaled.height + scaled.height % 2), (9, 11, 16, 255))
    canvas.alpha_composite(scaled)
    lines = []
    for y in range(0, canvas.height - 1, 2):
        parts = []
        for x in range(canvas.width):
            top = cast(tuple[int, int, int, int], canvas.getpixel((x, y)))
            bottom = cast(tuple[int, int, int, int], canvas.getpixel((x, y + 1)))
            parts.append(
                f"\x1b[38;2;{top[0]};{top[1]};{top[2]}m"
                f"\x1b[48;2;{bottom[0]};{bottom[1]};{bottom[2]}m▀"
            )
        lines.append("".join(parts) + RESET)
    return "\n".join(lines)
