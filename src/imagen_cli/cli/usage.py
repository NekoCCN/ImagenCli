"""User-correctable CLI mistakes: name the cause and the next command.

Only text built here is printed, so no parser or provider payload leaks.
"""

from __future__ import annotations

from typing import Any

from jsonschema import ValidationError

from imagen_cli import i18n


class UsageError(ValueError):
    """A mistake the caller can fix; safe to print verbatim."""

    def __init__(self, message: str, next_command: str = "") -> None:
        super().__init__(message)
        self.next_command = next_command

    def detail(self) -> dict[str, Any]:
        message = str(self)
        if self.next_command:
            message += i18n.t(
                f" Run `{self.next_command}` to see the available options.",
                f"运行 `{self.next_command}` 查看可用选项。",
            )
        return {
            "type": "validation_error",
            "message": message,
            "retryable": False,
            "accepted_maybe": False,
            "next_actions": (
                [{"command": self.next_command, "creates_paid_request": False}]
                if self.next_command
                else []
            ),
        }


def flag(name: str) -> str:
    """Schema property name as the flag a caller types."""
    return "--" + name.replace("_", "-")


def plain_detail(error: BaseException) -> dict[str, Any] | None:
    """Short single-line ValueError text is ours; anything else stays generic.

    Output redaction still runs on the final message, so a value that slipped
    through here is replaced before it reaches a terminal.
    """
    if not isinstance(error, ValueError):
        return None
    text = str(error).strip()
    if not text or len(text) > 200 or "\n" in text or "\x1b" in text:
        return None
    return {
        "type": "validation_error",
        "message": text,
        "retryable": False,
        "accepted_maybe": False,
        "next_actions": [],
    }


def _path(error: ValidationError) -> str:
    parts: list[str] = []
    for part in error.absolute_path:
        parts.append(f"[{part}]" if isinstance(part, int) else f".{part}" if parts else str(part))
    return "".join(parts)


def describe_pydantic(error: BaseException) -> UsageError | None:
    """Shared options are validated by pydantic, not by the command schema."""
    from pydantic import ValidationError as PydanticValidationError

    if not isinstance(error, PydanticValidationError):
        return None
    details = error.errors()
    if not details:
        return None
    first = details[0]
    name = flag(".".join(str(part) for part in first.get("loc", ())))
    kind = str(first.get("type", ""))
    limits = {
        "greater_than_equal": ">=",
        "less_than_equal": "<=",
        "greater_than": ">",
        "less_than": "<",
    }
    if kind in limits and isinstance(first.get("ctx"), dict):
        limit = next(iter(first["ctx"].values()), "")
        return UsageError(
            i18n.t(
                f"{name} must be {limits[kind]} {limit}.", f"{name} 必须 {limits[kind]} {limit}。"
            )
        )
    if kind == "missing":
        return UsageError(i18n.t(f"Missing required field: {name}.", f"缺少必填字段：{name}。"))
    return UsageError(i18n.t(f"{name} is not a valid value.", f"{name} 的取值不合法。"))


def describe_validation(error: ValidationError) -> UsageError:
    """Turn a schema rejection into field, rule and allowed values."""
    name = _path(error)
    shown = flag(name.split(".")[0]) if name and "." not in name else name or "input"
    value = error.validator
    rules = error.validator_value
    if value == "required":
        present = set(error.instance) if isinstance(error.instance, dict) else set()
        missing = [item for item in rules if item not in present] if isinstance(rules, list) else []
        names = ", ".join(flag(item) for item in missing) or shown
        return UsageError(i18n.t(f"Missing required field: {names}.", f"缺少必填字段：{names}。"))
    if value == "enum":
        allowed = ", ".join(str(item) for item in rules) if isinstance(rules, list) else ""
        return UsageError(
            i18n.t(
                f"{shown} must be one of: {allowed}.",
                f"{shown} 只能是以下之一：{allowed}。",
            )
        )
    if value in {"minimum", "exclusiveMinimum"}:
        limit = f"> {rules}" if value == "exclusiveMinimum" else f">= {rules}"
        return UsageError(i18n.t(f"{shown} must be {limit}.", f"{shown} 必须 {limit}。"))
    if value in {"maximum", "exclusiveMaximum"}:
        limit = f"< {rules}" if value == "exclusiveMaximum" else f"<= {rules}"
        return UsageError(i18n.t(f"{shown} must be {limit}.", f"{shown} 必须 {limit}。"))
    if value in {"minLength", "maxLength"}:
        limit = "at least" if value == "minLength" else "at most"
        return UsageError(
            i18n.t(
                f"{shown} must be {limit} {rules} characters long.",
                f"{shown} 长度必须{'至少' if value == 'minLength' else '至多'} {rules} 个字符。",
            )
        )
    if value == "pattern":
        return UsageError(
            i18n.t(
                f"{shown} has an invalid format (expected pattern {rules}).",
                f"{shown} 格式不合法（期望格式 {rules}）。",
            )
        )
    if value == "type":
        return UsageError(
            i18n.t(
                f"{shown} must be of type {rules}.",
                f"{shown} 的类型必须是 {rules}。",
            )
        )
    if value == "anyOf":
        branches = (
            [item for item in rules if isinstance(item, dict)]
            if isinstance(rules, list)
            else []
        )
        alternatives = [
            " / ".join(flag(key) for key in branch.get("required", []))
            for branch in branches
            if branch.get("required")
        ]
        if alternatives:
            joined = ", ".join(alternatives)
            return UsageError(
                i18n.t(
                    f"Provide one of: {joined}.",
                    f"请提供以下之一：{joined}。",
                )
            )
    if value == "additionalProperties":
        return UsageError(
            i18n.t(
                f"{shown} contains fields this command does not accept.",
                f"{shown} 含有该命令不接受的字段。",
            )
        )
    return UsageError(i18n.t(f"{shown} is not a valid value.", f"{shown} 的取值不合法。"))
