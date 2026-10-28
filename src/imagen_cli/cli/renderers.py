"""Machine renderers with one terminal result per invocation."""

import json
import sys
from datetime import UTC, datetime
from typing import Any, TextIO

from imagen_cli import i18n
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
    "safety_refusal": "the provider's content-safety policy rejected this request",
    "authentication_error": "the credential is invalid or missing",
    "permission_denied": "permission to this resource is denied",
    "validation_error": "the request parameters are invalid",
    "unsupported_capability": "this provider or model does not support the requested capability",
    "not_found": "no matching record exists in this workspace",
    "conflict": "the command conflicted and changed nothing",
    "wait_timeout": "the job did not finish before the timeout; it may still be running",
    "approval_required": "this step needs an explicit approval first",
    "provider_error": "the provider returned an error",
    "outcome_unknown": "cannot confirm whether the provider accepted the request "
    "(no automatic retry)",
    "internal_error": "a local execution error occurred",
    "canceled": "the command was canceled",
    "save_failed": "the result was returned but saving it locally failed",
}
ERROR_REASONS_ZH = {
    "safety_refusal": "上游内容安全策略拒绝了本次请求",
    "authentication_error": "凭据无效或缺失",
    "permission_denied": "没有访问该资源的权限",
    "validation_error": "请求参数不合法",
    "unsupported_capability": "当前供应商或模型不支持该能力",
    "not_found": "当前工作区中没有匹配的记录",
    "conflict": "命令发生冲突，未做任何改动",
    "wait_timeout": "任务在超时前未结束，可能仍在运行",
    "approval_required": "该步骤需要先显式批准",
    "provider_error": "供应商返回错误",
    "outcome_unknown": "无法确认供应商是否已受理（不自动重试）",
    "internal_error": "本地执行出错",
    "canceled": "命令已取消",
    "save_failed": "结果已返回，但本地保存失败",
}


def error_message(detail: Any) -> str | None:
    """Human reason for a stored job error, or None when there is no usable detail."""
    if not isinstance(detail, dict):
        return None
    reasons = ERROR_REASONS_ZH if i18n.is_chinese() else ERROR_REASONS
    label = reasons.get(str(detail.get("type")))
    if label is None:
        return None
    if isinstance(status := detail.get("http_status"), int):
        label += f"（HTTP {status}）" if i18n.is_chinese() else f" (HTTP {status})"
    if isinstance(exc := detail.get("error_class"), str) and exc:
        label += f" · {exc}"
    if isinstance(text := detail.get("detail"), str) and text:
        label += f" · {text}"
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
        reasons = ERROR_REASONS_ZH if i18n.is_chinese() else ERROR_REASONS
        error = {
            "type": str(detail.get("type"))
            if isinstance(detail, dict) and detail.get("type")
            else ERROR_TYPES.get(status, status),
            "message": error_message(detail)
            or reasons.get(status)
            or i18n.t("The command did not complete.", "命令未成功完成。"),
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
            from imagen_cli.cli.human import write

            write(redact(value), self.stdout)
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
