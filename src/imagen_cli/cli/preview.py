"""Optional inline preview: detection is conservative and failures stay silent."""

import os
import sys
from pathlib import Path
from typing import Any

__all__ = ["preview", "supports_preview"]

_TERMINALS = {"xterm-kitty", "xterm-256color", "screen-256color", "iTerm.app"}


def supports_preview(stream: Any = None) -> bool:
    stream = stream or sys.stdout
    return bool(
        getattr(stream, "isatty", lambda: False)()
        and os.environ.get("TERM") in _TERMINALS
        and os.environ.get("IMAGEN_PREVIEW") != "0"
    )


def preview(path: Path, stream: Any = None) -> bool:
    """Render a locator line only; any failure disables preview for this call."""
    stream = stream or sys.stdout
    try:
        if not supports_preview(stream) or not path.is_file():
            return False
        stream.write(f"[preview] {path}\n")
        stream.flush()
        return True
    except (OSError, ValueError):
        return False
