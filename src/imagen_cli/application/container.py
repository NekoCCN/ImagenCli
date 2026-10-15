"""Dependency composition root; offline commands have no provider or storage side effects."""

from dataclasses import dataclass, field
from functools import cached_property
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self

from imagen_cli.cli.options import SharedOptions

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
    options: SharedOptions = field(default_factory=SharedOptions)

    @cached_property
    def resolved_workspace(self) -> "Workspace":
        from imagen_cli.storage.workspace import resolve_workspace

        workspace = self.workspace if self.workspace is not None else self.options.workspace
        return resolve_workspace(workspace)

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

    def execute(
        self, command: str, args: list[str] | dict[str, Any] | None = None
    ) -> tuple[str, dict[str, Any]]:
        from imagen_cli.cli.registry import discover

        if command in {"help", "schema"}:
            if args is not None and not isinstance(args, list):
                raise ValueError("discovery requires a command target")
            target = next((arg for arg in (args or []) if arg != "--agent"), None)
            return "succeeded", discover(
                target, agent=command == "help" and "--agent" in (args or [])
            )
        if not isinstance(args, dict):
            return "unsupported_capability", {}
        if command in {"plan show", "plan approve"}:
            from imagen_cli.cli.commands import plan

            return plan.execute(self.repositories, command, args["plan_id"], args.get("digest"))
        if command in {"generate", "edit"}:
            from imagen_cli.application.preflight import plan_creation
            from imagen_cli.domain.contracts import CreationInput

            creation = CreationInput.model_validate(args)
            if creation.operation != command:
                raise ValueError("operation does not match command")
            return plan_creation(
                self.repositories, self.artifact_store, creation, dry_run=self.options.dry_run,
            )
        if command.startswith("profile "):
            from jsonschema import validate

            from imagen_cli.cli.commands import profile

            validate(args, discover(command)["input_schema"])
            data = profile.read_config(args["config_file"], args["profile"]) if (
                "config_file" in args
            ) else None
            return profile.execute(
                self.repositories, command, profile=args.get("profile"), data=data,
                limit=self.options.limit, cursor=self.options.cursor,
            )
        if command in {"providers", "models", "capabilities"}:
            from jsonschema import validate

            from imagen_cli.cli.commands import discovery

            validate(args, discover(command)["input_schema"])
            return discovery.list_(
                self.repositories, command, **args,
                limit=self.options.limit, cursor=self.options.cursor,
            )
        if command not in {
            "session create", "session list", "session show", "session resume",
            "job get", "job wait", "job fetch", "job reconcile",
        }:
            return "unsupported_capability", {}

        from jsonschema import validate

        from imagen_cli.application.artifact_service import ArtifactService
        from imagen_cli.application.session_service import SessionService
        from imagen_cli.cli.commands import job, session
        from imagen_cli.cli.input_readers import read_text

        validate(args, discover(command)["input_schema"])
        if command == "session create":
            # Read bounded, explicitly selected text before initializing storage.
            intent = read_text(args["intent_file"]) if "intent_file" in args else ""
            return session.create(
                SessionService(self.repositories, self.artifact_store),
                name=args["name"],
                intent=intent,
                profile=args.get("profile"),
                model=args.get("model"),
            )
        if command.startswith("session "):
            service = SessionService(self.repositories, self.artifact_store)
            if command == "session list":
                return session.list_(service, limit=self.options.limit, cursor=self.options.cursor)
            if command == "session show":
                return session.show(service, args["session"])
            return session.resume(service, args["session"])
        artifacts = ArtifactService(self.repositories, self.artifact_store)
        if command == "job wait":
            return job.wait_(artifacts, args["job_id"], timeout=self.options.timeout)
        handler = {"job get": job.get_, "job fetch": job.fetch_, "job reconcile": job.reconcile_}
        return handler[command](artifacts, args["job_id"])
