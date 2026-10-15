"""Dependency composition root; offline commands have no provider or storage side effects."""

from dataclasses import dataclass
from functools import cached_property
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from imagen_cli.storage.artifacts import ArtifactStore
    from imagen_cli.storage.database import ProcessConnection
    from imagen_cli.storage.private_state import PrivateStateStore
    from imagen_cli.storage.repositories import Repositories
    from imagen_cli.storage.workspace import Workspace


@dataclass
class Container:
    """Application boundary intentionally keeps provider dependencies optional."""

    workspace: str | None = None

    @cached_property
    def resolved_workspace(self) -> "Workspace":
        from imagen_cli.storage.workspace import resolve_workspace

        return resolve_workspace(self.workspace)

    @cached_property
    def connection(self) -> "ProcessConnection":
        from imagen_cli.storage.database import connect

        return connect(self.resolved_workspace.database)

    @cached_property
    def repositories(self) -> "Repositories":
        from imagen_cli.storage.repositories import Repositories

        return Repositories(self.connection)

    @cached_property
    def artifact_store(self) -> "ArtifactStore":
        from imagen_cli.storage.artifacts import ArtifactStore

        return ArtifactStore(self.resolved_workspace.artifacts, self.repositories)

    @cached_property
    def private_state_store(self) -> "PrivateStateStore":
        from imagen_cli.storage.private_state import PrivateStateStore

        return PrivateStateStore(self.resolved_workspace.private)

    def close(self) -> None:
        connection = self.__dict__.pop("connection", None)
        try:
            if connection is not None:
                connection.close()
        finally:
            for name in (
                "repositories", "artifact_store", "private_state_store", "resolved_workspace"
            ):
                self.__dict__.pop(name, None)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def execute(self, command: str, args: list[str] | None = None) -> tuple[str, dict[str, Any]]:
        from imagen_cli.cli.registry import discover

        if command == "help":
            target = next((arg for arg in (args or []) if arg != "--agent"), None)
            return "succeeded", discover(target, agent="--agent" in (args or []))
        if command == "schema":
            return "succeeded", discover(args[0] if args and not args[0].startswith("--") else None)
        return "unsupported_capability", {}
