"""Safe processing progress shared by computation and terminal interfaces."""

from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

ProgressCallback = Callable[[dict[str, Any]], None]
current_progress: ContextVar[ProgressCallback | None] = ContextVar(
    "processing_progress", default=None,
)


def report(callback: ProgressCallback | None, stage: str, *, completed: int = 0,
           total: int | None = None, unit: str | None = None, operation: str = "") -> None:
    if callback is not None:
        callback({"stage": stage, "completed": completed, "total": total,
                  "unit": unit, "operation": operation})
