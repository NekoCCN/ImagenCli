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
# Why an attempt failed, in words; provider response text is never echoed.
ERROR_REASONS = {
    "safety_refusal": "上游内容安全策略拒绝了本次请求",
    "authentication_error": "凭据无效或缺失",
    "permission_denied": "没有访问该资源的权限",
    "validation_error": "请求参数不合法",
    "provider_error": "供应商返回错误",
    "outcome_unknown": "无法确认供应商是否已受理（不自动重试）",
    "internal_error": "本地执行出错",
    "save_failed": "结果已返回，但本地保存失败",
}


def error_message(detail: Any) -> str | None:
    """Human reason for a stored job error, or None when there is no usable detail."""
    if not isinstance(detail, dict):
        return None
    label = ERROR_REASONS.get(str(detail.get("type")))
    if label is None:
        return None
    if isinstance(status := detail.get("http_status"), int):
        label += f"（HTTP {status}）"
    if isinstance(exc := detail.get("error_class"), str) and exc:
        label += f" · {exc}"
    return label


def data_error(data: Any) -> Any:
    """The stored failure detail, from the top level or the job facts inside data."""
    if not isinstance(data, dict):
        return None
    if isinstance(detail := data.get("error"), dict):
        return detail
    job = data.get("job")
    return job.get("error") if isinstance(job, dict) else None


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
        detail = data_error(data)
        error = {
            "type": str(detail.get("type")) if isinstance(detail, dict) and detail.get("type")
            else ERROR_TYPES.get(status, status),
            "message": error_message(detail) or "命令未成功完成。",
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
