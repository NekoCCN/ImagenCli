"""Stable state vocabularies. Job state deliberately excludes partial success."""

from enum import IntEnum, StrEnum


class CapabilityStatus(StrEnum):
    TESTED = "T"
    IMPLEMENTED = "I"
    DECLARED = "D"
    NOT_SUPPORTED = "N"
    UNKNOWN = "U"


class Operation(StrEnum):
    GENERATE = "generate"
    EDIT = "edit"
    TRANSFORM = "transform"
    SUPERRES = "superres"


class ArtifactRole(StrEnum):
    INPUT = "input"
    MASK = "mask"
    REFERENCE = "reference"
    OUTPUT = "output"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    UNKNOWN = "unknown"


class CreationState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    UNKNOWN = "unknown"
    PARTIAL_SUCCESS = "partial_success"


class DispatchState(StrEnum):
    NOT_STARTED = "not_started"
    INTENT_RECORDED = "intent_recorded"
    REMOTE_IDENTIFIED = "remote_identified"
    RESPONSE_RECEIVED = "response_received"


class RemoteState(StrEnum):
    NOT_SUBMITTED = "not_submitted"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    UNKNOWN = "unknown"


class LocalSaveState(StrEnum):
    NOT_STARTED = "not_started"
    PENDING = "pending"
    SAVING = "saving"
    SAVED = "saved"
    PARTIAL = "partial"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class PublicationState(StrEnum):
    NOT_ATTEMPTED = "not_attempted"
    PUBLISHED = "published"
    CONFLICT = "conflict"


class ApprovalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    CONSUMED = "consumed"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"


class ArtifactSaveState(StrEnum):
    STAGING = "staging"
    PUBLISHED = "published"
    SAVE_FAILED = "save_failed"


class ResultStatus(StrEnum):
    SUCCEEDED = "succeeded"
    ACCEPTED = "accepted"
    APPROVED = "approved"
    PLANNED = "planned"
    INTERNAL_ERROR = "internal_error"
    VALIDATION_ERROR = "validation_error"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    AUTHENTICATION_ERROR = "authentication_error"
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    WAIT_TIMEOUT = "wait_timeout"
    UNKNOWN = "unknown"
    APPROVAL_REQUIRED = "approval_required"
    FAILED = "failed"
    SAVE_FAILED = "save_failed"
    CANCELED = "canceled"
    SAFETY_REFUSAL = "safety_refusal"
    PARTIAL_SUCCESS = "partial_success"


class ErrorType(StrEnum):
    VALIDATION_ERROR = "validation_error"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    AUTHENTICATION_ERROR = "authentication_error"
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    WAIT_TIMEOUT = "wait_timeout"
    OUTCOME_UNKNOWN = "outcome_unknown"
    APPROVAL_REQUIRED = "approval_required"
    PROVIDER_ERROR = "provider_error"
    TRANSPORT_ERROR = "transport_error"
    SAVE_FAILED = "save_failed"
    CANCELED = "canceled"
    SAFETY_REFUSAL = "safety_refusal"
    PARTIAL_FAILURE = "partial_failure"
    INTERNAL_ERROR = "internal_error"


class ExitCode(IntEnum):
    SUCCESS = 0
    INTERNAL_ERROR = 1
    VALIDATION_ERROR = 2
    AUTHENTICATION_ERROR = 3
    NOT_FOUND = 4
    CONFLICT = 5
    UNCERTAIN = 6
    EXECUTION_FAILED = 7
    SAFETY_REFUSAL = 8
    PARTIAL_SUCCESS = 9
