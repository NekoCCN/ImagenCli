"""Worker lifecycle commands; recover observes, it never dispatches."""

from pathlib import Path
from typing import Any

from imagen_cli.execution import worker as execution
from imagen_cli.storage.repositories import Repositories


def status(repositories: Repositories) -> tuple[str, dict[str, Any]]:
    return execution.status(repositories)


def start(repositories: Repositories, workspace: Path) -> tuple[str, dict[str, Any]]:
    if not execution.detached_supported():
        return "unsupported_capability", {}
    try:
        row = execution.spawn_worker(repositories, workspace)
    except execution.LeaseError:
        return "failed", {}
    return "succeeded", {"worker": row}


def recover(repositories: Repositories) -> tuple[str, dict[str, Any]]:
    worker = execution.Worker(repositories)
    try:
        worker.start()
    except execution.LeaseError:
        return "unsupported_capability", {}
    try:
        return "succeeded", {"recovered": worker.recover()}
    finally:
        worker.stop()
