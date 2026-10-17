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
        if command in {"worker start", "worker status", "worker recover"}:
            from imagen_cli.cli.commands import worker

            if command == "worker status":
                return worker.status(self.repositories)
            if command == "worker start":
                return worker.start(self.repositories, self.resolved_workspace.root)
            return worker.recover(self.repositories)
        if command.startswith("batch "):
            from jsonschema import validate

            from imagen_cli.application.batch_service import BatchService
            from imagen_cli.cli.commands import batch

            validate(args, discover(command)["input_schema"])
            if command != "batch submit":
                batch_service = BatchService(self.repositories)
                if command == "batch get":
                    return batch.get_(batch_service, args["batch_id"])
                return batch.wait_(
                    batch_service, args["batch_id"], timeout=self.options.timeout,
                )
            from imagen_cli.application.planning_service import PlanningService
            from imagen_cli.application.preflight import plan_creation
            from imagen_cli.domain.contracts import CreationInput

            def prepare(creation: CreationInput) -> dict[str, Any]:
                status, data = plan_creation(
                    self.repositories, self.artifact_store, creation,
                    asynchronous=self.options.async_,
                )
                if status != "approval_required" or "plan" not in data:
                    raise ValueError("batch child is not executable")
                plan = data["plan"]
                if not isinstance(plan, dict):
                    raise ValueError("batch child plan is unavailable")
                PlanningService(self.repositories).approve(plan["id"], plan["plan_digest"])
                return {str(key): value for key, value in plan.items()}

            def provider_limit(plan: dict[str, Any]) -> int | None:
                snapshot = plan["target_snapshot"]
                records = self.repositories.list("capabilities", limit=1000)
                for record in records:
                    if (
                        record.get("provider") == snapshot.get("provider")
                        and record.get("family") == snapshot.get("family")
                        and record.get("model") == plan.get("model")
                        and record.get("model_revision") == snapshot.get("model_revision")
                        and record.get("operation") == plan.get("operation")
                    ):
                        value = (record.get("input_limits") or {}).get("max_concurrency")
                        if type(value) is int and not isinstance(value, bool):
                            return value
                return None

            def run(plan: dict[str, Any], index: int) -> str:
                # Each child gets its own connection; the parent connection stays single-threaded.
                with Container(
                    workspace=str(self.resolved_workspace.root), options=self.options,
                ) as child:
                    _, data = child.execute("plan execute", {"plan_id": plan["id"]})
                job = data.get("job")
                if not isinstance(job, dict):
                    raise ValueError(f"batch child {index} did not create a job")
                identity = job.get("id")
                if not isinstance(identity, str):
                    raise ValueError(f"batch child {index} did not create a job")
                return identity

            return batch.submit(
                BatchService(
                    self.repositories, prepare=prepare, run=run,
                    provider_limit=provider_limit,
                ),
                args["requests"], args.get("concurrency", 2),
            )
        if command == "plan execute":
            from imagen_cli.application.artifact_service import ArtifactService
            from imagen_cli.application.execution_service import ExecutionService
            from imagen_cli.application.outcome_service import OutcomeService
            from imagen_cli.capabilities.catalog import CapabilityCatalog
            from imagen_cli.capabilities.validator import CapabilityValidator
            from imagen_cli.cli.commands import job
            from imagen_cli.execution.configured import ConfiguredAdapter
            from imagen_cli.execution.foreground import ForegroundExecutor

            row = self.repositories.get("plans", args["plan_id"], include_private=True)
            if row is None:
                return "not_found", {}
            records: list[dict[str, Any]] = []
            while True:
                page = self.repositories.list("capabilities", limit=1000, offset=len(records))
                records.extend(page)
                if len(page) < 1000:
                    break
            executor = ForegroundExecutor(
                self.repositories, ConfiguredAdapter(
                    self.repositories, timeout=self.options.timeout,
                    private_store=self.private_state_store,
                ),
                ArtifactService(self.repositories, self.artifact_store),
                OutcomeService(self.repositories), self.private_state_store,
            )
            facts = ExecutionService(
                self.repositories, CapabilityValidator(CapabilityCatalog(records)),
                self.artifact_store, executor,
            ).execute_approved(
                row["id"], row["plan_digest"],
                model_revision=row["target_snapshot"]["model_revision"],
                input_artifact_ids=tuple(row["portable_context"].get("input_artifact_ids", [])),
                mask_artifact_id=row["portable_context"].get("mask_artifact_id"),
            )
            return facts["status"], {
                "job": facts["job"],
                "turn": facts["turn"],
                "artifacts": job.get_(
                    ArtifactService(self.repositories, self.artifact_store), facts["job"]["id"]
                )[1]["artifacts"],
            }
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
                self.repositories, self.artifact_store, creation,
                dry_run=self.options.dry_run, asynchronous=self.options.async_,
            )
        if command == "tui":
            try:
                from imagen_cli.cli.tui.app import Deps, WorkbenchApp
            except ImportError as error:  # optional extra
                raise ValueError("tui extra is not installed") from error
            WorkbenchApp(
                Deps(
                    self.repositories, self.artifact_store,
                    private_store=self.private_state_store,
                    workspace_root=self.resolved_workspace.root,
                ),
                workspace_label=str(self.resolved_workspace.root),
            ).run()
            return "succeeded", {}
        if command == "transform":
            from jsonschema import validate

            from imagen_cli.cli.commands import postprocess

            validate(args, discover(command)["input_schema"])
            return postprocess.transform_(
                self.repositories, self.artifact_store, args["artifact"],
                pipeline=args.get("pipeline"), op=args.get("op"), session=args.get("session"),
            )
        if command.startswith("superres "):
            from jsonschema import validate

            from imagen_cli.application import superres

            validate(args, discover(command)["input_schema"])
            if command == "superres models":
                return "succeeded", superres.catalog()
            if command == "superres fetch":
                return "succeeded", superres.fetch(
                    args["model"], accept=args.get("accept") is True,
                )
            selected = [
                item.strip() for item in args["model"].split(",") if item.strip()
            ]
            return "succeeded", superres.benchmark(
                self.repositories, self.artifact_store, args["artifact"], selected,
                backend=args.get("backend"),
                tile=int(args.get("tile", 256)), overlap=int(args.get("overlap", 16)),
            )
        if command == "ops":
            from imagen_cli.application import media_ops

            return "succeeded", {"operations": sorted(media_ops.OPS)}
        if command == "sheet":
            from jsonschema import validate

            from imagen_cli.application.sheet_service import build as build_sheet

            validate(args, discover(command)["input_schema"])
            sources = [item.strip() for item in args["artifacts"].split(",") if item.strip()]
            return "succeeded", build_sheet(
                self.repositories, self.artifact_store, sources,
                session=args.get("session"),
                columns=int(args.get("columns", 3)), cell=int(args.get("cell", 256)),
            )
        if command == "compare":
            from jsonschema import validate

            from imagen_cli.application.quality_service import compare

            validate(args, discover(command)["input_schema"])
            return "succeeded", compare(
                self.repositories, self.artifact_store, args["first"], args["second"]
            )
        if command.startswith("models "):
            from jsonschema import validate

            from imagen_cli.cli.commands import models
            from imagen_cli.execution.configured import resolve_credential

            validate(args, discover(command)["input_schema"])
            identity = args["profile"]
            if command == "models refresh":
                return models.refresh_(
                    self.repositories, identity,
                    send_credential=args.get("send_credential") is True,
                    resolve_credential=resolve_credential,
                )
            if command == "models add":
                return models.add_(self.repositories, identity, args["id"])
            return models.list_(self.repositories, identity)
        if command.startswith("profile "):
            from jsonschema import validate

            from imagen_cli.cli.commands import profile

            validate(args, discover(command)["input_schema"])
            if "config_file" in args:
                data = profile.read_config(args["config_file"], args["profile"])
            elif command == "profile set":
                data = profile.build_set(args)
            else:
                data = None
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
        if command in {
            "session fork", "session export", "session import", "session delete", "asset gc",
        }:
            from jsonschema import validate

            from imagen_cli.cli.commands import history

            validate(args, discover(command)["input_schema"])
            if command == "session fork":
                return history.fork_(
                    self.repositories, args["session"],
                    turn_id=args["from_turn"], name=args["name"],
                    artifact_ids=(args["artifact"],) if "artifact" in args else (),
                )
            if command == "session export":
                return history.export_(
                    self.repositories, self.artifact_store, args["session"], args["to"],
                )
            if command == "session import":
                return history.import_(
                    self.repositories, self.artifact_store, args["archive"],
                )
            if command == "session delete":
                return history.delete_(
                    self.repositories, args["session"], yes=args.get("yes") is True,
                )
            return history.gc_(
                self.repositories, self.artifact_store,
                dry_run=args.get("dry_run") is True, plan=args.get("plan"),
                yes=args.get("yes") is True,
            )
        if command.startswith("cache "):
            from jsonschema import validate

            from imagen_cli.cli.commands import cache

            validate(args, discover(command)["input_schema"])
            if command == "cache audit":
                return "succeeded", cache.audit_(
                    self.repositories, self.artifact_store, self.private_state_store,
                )
            return "succeeded", cache.purge_(
                self.repositories, self.artifact_store,
                plan=args.get("plan"), yes=args.get("yes") is True,
                private_store=self.private_state_store,
            )
        if command not in {
            "session create", "session list", "session show", "session resume",
            "job get", "job wait", "job fetch", "job reconcile", "job cancel",
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
        if command == "job cancel":
            return job.cancel_(artifacts, args["job_id"], scope=args["scope"])
        if command == "job wait":
            return job.wait_(artifacts, args["job_id"], timeout=self.options.timeout)
        if command in {"job fetch", "job reconcile"}:
            from imagen_cli.application.reconcile import recover_inline

            try:
                recover_inline(artifacts, self.private_state_store, args["job_id"])
            except (OSError, ValueError):
                # Keep the durable remote fact and report remaining local save failures.
                pass
        handler = {"job get": job.get_, "job fetch": job.fetch_, "job reconcile": job.reconcile_}
        return handler[command](artifacts, args["job_id"])
