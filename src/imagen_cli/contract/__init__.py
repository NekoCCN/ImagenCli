"""Shared remote-job contract: statuses, canonical JSON, digest and derived paths."""

from .canonical import canonical
from .digest import DIGEST_FIELDS, job_digest
from .jobs import job_paths, reject_client_paths
from .status import RESUMABLE, RETRY_INTERRUPTED, JobStatus

__all__ = [
    "DIGEST_FIELDS",
    "JobStatus",
    "RESUMABLE",
    "RETRY_INTERRUPTED",
    "canonical",
    "job_digest",
    "job_paths",
    "reject_client_paths",
]
