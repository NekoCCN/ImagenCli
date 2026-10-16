"""Truthful batch aggregates; child facts stay untouched and unknown is not terminal."""

from collections.abc import Mapping, Sequence
from typing import Any

_ACTIVE = {"queued", "running"}


def _complete(child: Mapping[str, Any]) -> bool:
    saved = child.get("saved_artifact_count", 0)
    actual = child.get("actual_count", 0)
    return (
        child.get("state") == "succeeded"
        and child.get("remote_state") == "succeeded"
        and child.get("local_save_state") == "saved"
        and type(saved) is int
        and type(actual) is int
        and actual > 0
        and saved >= actual
    )


def _partial(child: Mapping[str, Any]) -> bool:
    saved = child.get("saved_artifact_count", 0)
    return (
        not _complete(child)
        and (child.get("local_save_state") == "partial" or (type(saved) is int and saved > 0))
    )


def summarize(children: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Apply the six-step priority; success counts jobs, saved count counts files."""
    complete_count = sum(1 for child in children if _complete(child))
    saved_count = sum(
        child.get("saved_artifact_count", 0)
        for child in children
        if type(child.get("saved_artifact_count", 0)) is int
    )
    unknown = any(
        child.get("state") == "unknown" or child.get("remote_state") == "unknown"
        for child in children
    )
    partial = any(_partial(child) for child in children)
    if any(child.get("state") in _ACTIVE for child in children):
        state = "running"
    elif children and complete_count == len(children):
        state = "succeeded"
    elif complete_count:
        # Any non-complete child after the active check leaves a mixed outcome.
        state = "partial_success"
    elif partial:
        state = "unknown" if unknown else "partial_success"
    elif unknown:
        state = "unknown"
    elif any(child.get("state") == "failed" for child in children):
        state = "failed"
    elif children and all(child.get("state") == "canceled" for child in children):
        state = "canceled"
    else:
        state = "failed"
    return {
        "state": state,
        "child_count": len(children),
        "complete_success_count": complete_count,
        "saved_artifact_count": saved_count,
    }
