"""Machine renderers with one terminal result per invocation."""

import json
import sys
from datetime import UTC, datetime
from typing import Any, TextIO

from imagen_cli.domain.contracts import InvocationEvent

from .diagnostics import redact

CODES = {
    "succeeded": 0,
    "accepted": 0,
    "approved": 0,
    "planned": 0,
    "validation_error": 2,
    "unsupported_capability": 2,
    "authentication_error": 3,
    "permission_denied": 3,
    "not_found": 4,
    "conflict": 5,
    "wait_timeout": 6,
    "unknown": 6,
    "approval_required": 6,
    "failed": 7,
    "save_failed": 7,
    "canceled": 7,
    "safety_refusal": 8,
    "partial_success": 9,
}
ERROR_TYPES = {
    "unknown": "outcome_unknown",
    "partial_success": "partial_failure",
    "failed": "provider_error",
}


def result(
    status: str = "internal_error",
    data: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if status in {"succeeded", "accepted", "approved", "planned"}:
        return {
            "schema_version": 1,
            "ok": True,
            "status": status,
            "data": data or {},
            "error": None,
        }
    if error is None:
        error = {
            "type": ERROR_TYPES.get(status, status),
            "message": "命令未成功完成。",
            "retryable": False,
            "accepted_maybe": status in {"unknown", "partial_success"},
            "next_actions": [],
        }
    return {"schema_version": 1, "ok": False, "status": status, "data": data or {}, "error": error}


class Renderer:
    def __init__(
        self, mode: str, stdout: TextIO | None = None, stderr: TextIO | None = None
    ) -> None:
        self.mode, self.stdout, self.stderr = mode, stdout or sys.stdout, stderr or sys.stderr
        self.seq = 0
        self.ended = False

    def finish(self, value: dict[str, Any]) -> int:
        if self.ended:
            raise ValueError("renderer already ended")
        if self.mode == "jsonl":
            self.emit_event(
                {
                    "schema_version": 1,
                    "seq": self.seq + 1,
                    "event": "terminal",
                    "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "job_id": None,
                    "data": {},
                    "result": value,
                }
            )
        elif self.mode == "human":
            from imagen_cli.cli.human import color_enabled, render

            safe = redact(value)
            self.stdout.write(render(safe, color=color_enabled(self.stdout)) + "\n")
            self.stdout.flush()
            self.ended = True
        else:
            self._write(value)
            self.ended = True
        return CODES.get(value["status"], 1)

    def _write(self, value: dict[str, Any]) -> None:
        self.stdout.write(
            json.dumps(redact(value), ensure_ascii=False, separators=(",", ":")) + "\n"
        )
        self.stdout.flush()

    def emit_event(self, event: dict[str, Any]) -> None:
        if self.ended:
            raise ValueError("renderer already ended")
        InvocationEvent.model_validate(event)
        if event.get("seq") != self.seq + 1:
            raise ValueError("event sequence must be contiguous")
        self.seq += 1
        self._write(event)
        if event.get("event") == "terminal":
            self.ended = True

    def start(self, command: str) -> None:
        self.emit_event(
            {
                "schema_version": 1,
                "seq": 1,
                "event": "started",
                "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "job_id": None,
                "data": {"command": command},
            }
        )
