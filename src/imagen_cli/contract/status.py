"""Terminal and recovery states for a remote job."""

from enum import StrEnum


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"
    CANCELED = "canceled"


TERMINAL = frozenset({
    JobStatus.SUCCEEDED,
    JobStatus.FAILED,
    JobStatus.INTERRUPTED,
    JobStatus.UNKNOWN,
    JobStatus.CANCELED,
})

RESUMABLE = frozenset({JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.SUCCEEDED})

RETRY_INTERRUPTED = frozenset({JobStatus.INTERRUPTED})
