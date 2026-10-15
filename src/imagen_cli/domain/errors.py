"""Stable domain errors and result exit mapping."""

from .enums import ExitCode

_STATUS_CODES = {
    "internal_error": ExitCode.INTERNAL_ERROR,
    "validation_error": ExitCode.VALIDATION_ERROR,
    "unsupported_capability": ExitCode.VALIDATION_ERROR,
    "authentication_error": ExitCode.AUTHENTICATION_ERROR,
    "permission_denied": ExitCode.AUTHENTICATION_ERROR,
    "not_found": ExitCode.NOT_FOUND,
    "conflict": ExitCode.CONFLICT,
    "wait_timeout": ExitCode.UNCERTAIN,
    "unknown": ExitCode.UNCERTAIN,
    "approval_required": ExitCode.UNCERTAIN,
    "failed": ExitCode.EXECUTION_FAILED,
    "save_failed": ExitCode.EXECUTION_FAILED,
    "canceled": ExitCode.EXECUTION_FAILED,
    "safety_refusal": ExitCode.SAFETY_REFUSAL,
    "partial_success": ExitCode.PARTIAL_SUCCESS,
}


def exit_code_for(status: str) -> int:
    return int(_STATUS_CODES[status])


class DomainError(ValueError):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status
        self.exit_code = exit_code_for(status)
