"""Dependency composition root; offline commands have no provider or storage side effects."""

from dataclasses import dataclass, field
from functools import cached_property, partial
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self

from imagen_cli import i18n
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
                "repositories",
                "artifact_store",
                "private_state_store",
                "resolved_workspace",
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
                    batch_service,
                    args["batch_id"],
                    timeout=self.options.timeout,
                )
            from imagen_cli.application.planning_service import PlanningService
            from imagen_cli.application.preflight import plan_creation
            from imagen_cli.domain.contracts import CreationInput

            def prepare(creation: CreationInput) -> dict[str, Any]:
                status, data = plan_creation(
                    self.repositories,
                    self.artifact_store,
                    creation,
                    asynchronous=self.options.async_,
                )
                if status != "approval_required" or "plan" not in data:
                    raise ValueError("batch child is not executable")
                plan = data["plan"]
                if not isinstance(plan, dict):
                    raise ValueError("batch child plan is unavailable")
                PlanningService(self.repositories).approve(plan["id"], plan["plan_digest"])
                return {str(key): value for key, value in plan.items()}

            def run(plan: dict[str, Any], index: int) -> str:
                # Each child gets its own connection; the parent connection stays single-threaded.
                with Container(
                    workspace=str(self.resolved_workspace.root),
                    options=self.options,
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
                BatchService(self.repositories, prepare=prepare, run=run),
                args["requests"],
                args.get("concurrency", 2),
            )
        if command == "plan execute":
            from imagen_cli.application.artifact_service import ArtifactService
            from imagen_cli.application.execution_service import ExecutionService
            from imagen_cli.application.outcome_service import OutcomeService
            from imagen_cli.cli.commands import job
            from imagen_cli.execution.configured import ConfiguredAdapter
            from imagen_cli.execution.foreground import ForegroundExecutor

            row = self.repositories.get("plans", args["plan_id"], include_private=True)
            if row is None:
                return "not_found", {}
            executor = ForegroundExecutor(
                self.repositories,
                ConfiguredAdapter(
                    self.repositories,
                    timeout=self.options.timeout,
                    private_store=self.private_state_store,
                ),
                ArtifactService(self.repositories, self.artifact_store),
                OutcomeService(self.repositories),
                self.private_state_store,
            )
            facts = ExecutionService(
                self.repositories,
                self.artifact_store,
                executor,
            ).execute_approved(
                row["id"],
                row["plan_digest"],
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
        if command == "plan retry":
            from imagen_cli.application.preflight import plan_creation
            from imagen_cli.domain.contracts import CreationInput

            row = self.repositories.get("plans", args["plan_id"], include_private=True)
            if row is None:
                return "not_found", {}
            session_row = self.repositories.get("sessions", row["session_id"])
            if session_row is None:
                return "not_found", {}
            jobs = self.repositories.list("jobs", plan_id=row["id"])
            if jobs and (
                jobs[0].get("state") not in {"failed", "canceled"} or jobs[0].get("accepted_maybe")
            ):
                # A succeeded, unknown or in-flight attempt must not be rebuilt implicitly.
                existing = jobs[0]
                return "conflict", {
                    "job": existing,
                    "next_actions": [
                        {"command": f"job get {existing['id']}", "creates_paid_request": False},
                        {
                            "command": f"job reconcile {existing['id']}",
                            "creates_paid_request": False,
                        },
                    ],
                }
            context = row.get("portable_context") or {}
            parameters = dict(row.get("normalized_parameters") or {})
            count = parameters.pop("count", 1)
            size = parameters.pop("size", None)
            creation = CreationInput(
                schema_version=1,
                operation=row["operation"],
                session=session_row["name"],
                prompt=row["prompt_snapshot"],
                profile=row["target_snapshot"]["profile_identity"],
                model=row["model"],
                inputs=["artifact:" + item for item in context.get("input_artifact_ids", [])],
                count=count,
                provider_params=parameters,
                **({"size": size} if size is not None else {}),
            )
            return plan_creation(
                self.repositories,
                self.artifact_store,
                creation,
                dry_run=self.options.dry_run,
                asynchronous=self.options.async_,
            )
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
                self.repositories,
                self.artifact_store,
                creation,
                dry_run=self.options.dry_run,
                asynchronous=self.options.async_,
            )
        if command == "tui":
            try:
                from imagen_cli.cli.tui.app import Deps, WorkbenchApp
            except ImportError as error:  # optional extra
                raise ValueError("tui extra is not installed") from error
            WorkbenchApp(
                Deps(
                    self.repositories,
                    self.artifact_store,
                    execute=self.execute,
                    private_store=self.private_state_store,
                    workspace_root=self.resolved_workspace.root,
                    options=self.options,
                ),
                workspace_label=str(self.resolved_workspace.root),
            ).run()
            return "succeeded", {}
        if command == "transform":
            from jsonschema import validate

            from imagen_cli.cli.commands import postprocess

            validate(args, discover(command)["input_schema"])
            return postprocess.transform_(
                self.repositories,
                self.artifact_store,
                args["artifact"],
                pipeline=args.get("pipeline"),
                op=args.get("op"),
                preset=args.get("preset"),
                session=args.get("session"),
            )
        if command.startswith("superres "):
            from jsonschema import validate

            from imagen_cli.application import superres

            validate(args, discover(command)["input_schema"])
            if command == "superres models":
                return "succeeded", superres.catalog()
            if command == "superres fetch":
                return "succeeded", superres.fetch(
                    args["model"],
                    accept=args.get("accept") is True,
                )
            selected = [item.strip() for item in args["model"].split(",") if item.strip()]
            return "succeeded", superres.benchmark(
                self.repositories,
                self.artifact_store,
                args["artifact"],
                selected,
                backend=args.get("backend"),
                tile=int(args.get("tile", 256)),
                overlap=int(args.get("overlap", 16)),
            )
        if command == "ops":
            from imagen_cli.application import media_ops, operator_catalog

            data: dict[str, Any] = {
                "operations": sorted(media_ops.OPS),
                "presets": sorted(operator_catalog.PRESET_PIPELINES),
            }
            target_op = args.get("op")
            target_preset = args.get("preset")
            verbose = args.get("verbose") is True
            if target_op or target_preset or verbose:
                details: dict[str, Any] = {}
                if target_op:
                    intro = operator_catalog.localized_intro(target_op)
                    if intro is not None:
                        details[target_op] = intro
                    elif op_item := operator_catalog.get_operator_item(target_op):
                        shown = operator_catalog.localized_operator(op_item)
                        details[target_op] = {
                            "title": shown.name,
                            "summary": shown.description,
                            "principle": shown.category_label,
                            "scenarios": "CLI / TUI interactive parameter tuning"
                            if not i18n.is_chinese()
                            else "CLI / TUI 交互调参",
                        }
                elif target_preset:
                    match_key = next(
                        (
                            k
                            for k in operator_catalog.PRESET_PIPELINES
                            if target_preset.lower() in k.lower()
                        ),
                        None,
                    )
                    if match_key:
                        intro_data = operator_catalog.localized_intro(match_key) or {}
                        details[match_key] = {
                            "pipeline": operator_catalog.PRESET_PIPELINES[match_key],
                            "intro": intro_data,
                        }
                else:
                    details = {
                        key: operator_catalog.localized_intro(key) or {}
                        for key in operator_catalog.INTROS
                    }
                data["details"] = details
            return "succeeded", data
        if command == "sheet":
            from jsonschema import validate

            from imagen_cli.application.sheet_service import build as build_sheet

            validate(args, discover(command)["input_schema"])
            sources = [item.strip() for item in args["artifacts"].split(",") if item.strip()]
            return "succeeded", build_sheet(
                self.repositories,
                self.artifact_store,
                sources,
                session=args.get("session"),
                columns=int(args.get("columns", 3)),
                cell=int(args.get("cell", 256)),
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

            validate(args, discover(command)["input_schema"])
            identity = args["profile"]
            if command == "models refresh":
                from imagen_cli.execution.configured import resolve_credential

                return models.refresh_(
                    self.repositories,
                    identity,
                    send_credential=args.get("send_credential") is True,
                    resolve_credential=partial(
                        resolve_credential, private_store=self.private_state_store
                    ),
                )
            if command == "models add":
                return models.add_(self.repositories, identity, args["id"])
            if command == "models use":
                return models.use_(self.repositories, identity, args["id"])
            return models.list_(self.repositories, identity)
        if command.startswith("profile "):
            from jsonschema import validate

            from imagen_cli.cli.commands import profile

            validate(args, discover(command)["input_schema"])
            if command == "profile secret":
                return profile.secret(
                    self.repositories,
                    self.private_state_store,
                    args["profile"],
                    args["key_file"],
                )
            profile_data: dict[str, Any] | None = None
            if "config_file" in args:
                profile_data = profile.read_config(args["config_file"], args["profile"])
            elif command == "profile set":
                profile_data = profile.build_set(args)
            return profile.execute(
                self.repositories,
                command,
                profile=args.get("profile"),
                data=profile_data,
                limit=self.options.limit,
                cursor=self.options.cursor,
            )
        if command in {"providers", "models"}:
            from jsonschema import validate

            from imagen_cli.cli.commands import discovery

            validate(args, discover(command)["input_schema"])
            return discovery.list_(
                self.repositories,
                command,
                **args,
                limit=self.options.limit,
                cursor=self.options.cursor,
            )
        if command in {
            "session fork",
            "session export",
            "session import",
            "session delete",
            "session remove-artifact",
            "session remove-image",
            "asset gc",
        }:
            from jsonschema import validate

            from imagen_cli.cli.commands import history

            validate(args, discover(command)["input_schema"])
            if command in {"session remove-artifact", "session remove-image"}:
                return history.remove_artifact_(
                    self.repositories,
                    self.artifact_store,
                    args["session"],
                    args["artifact"],
                    purge=args.get("purge") is not False,
                )
            if command == "session fork":
                return history.fork_(
                    self.repositories,
                    args["session"],
                    turn_id=args["from_turn"],
                    name=args["name"],
                    artifact_ids=(args["artifact"],) if "artifact" in args else (),
                )
            if command == "session export":
                return history.export_(
                    self.repositories,
                    self.artifact_store,
                    args["session"],
                    args["to"],
                )
            if command == "session import":
                return history.import_(
                    self.repositories,
                    self.artifact_store,
                    args["archive"],
                )
            if command == "session delete":
                return history.delete_(
                    self.repositories,
                    args["session"],
                    yes=args.get("yes") is True,
                )
            return history.gc_(
                self.repositories,
                self.artifact_store,
                dry_run=args.get("dry_run") is True,
                plan=args.get("plan"),
                yes=args.get("yes") is True,
            )
        if command in {"asset show", "asset delete", "asset remove"}:
            from jsonschema import validate

            from imagen_cli.cli.commands import asset

            validate(args, discover(command)["input_schema"])
            if command in {"asset delete", "asset remove"}:
                return asset.delete_(
                    self.repositories,
                    self.artifact_store,
                    args["artifact"],
                )
            return asset.show_(
                self.repositories,
                self.artifact_store,
                args["artifact"],
            )
        if command == "asset open":
            from jsonschema import validate

            from imagen_cli.application.opener import open_path

            validate(args, discover(command)["input_schema"])
            row = self.repositories.get("artifacts", args["artifact_id"])
            path = self.artifact_store.get(args["artifact_id"])
            if row is None or path is None:
                return "not_found", {}
            folder = args.get("folder") is True
            open_path(path, folder=folder)
            return "succeeded", {
                "artifact": row["id"],
                "path": str(path.parent if folder else path),
                "opened": "folder" if folder else "image",
            }
        if command.startswith("cache "):
            from jsonschema import validate

            from imagen_cli.cli.commands import cache

            validate(args, discover(command)["input_schema"])
            if command == "cache audit":
                return "succeeded", cache.audit_(
                    self.repositories,
                    self.artifact_store,
                    self.private_state_store,
                )
            return "succeeded", cache.purge_(
                self.repositories,
                self.artifact_store,
                plan=args.get("plan"),
                yes=args.get("yes") is True,
                private_store=self.private_state_store,
            )
        if command.startswith("skills "):
            from jsonschema import validate

            from imagen_cli.cli.commands import skills

            validate(args, discover(command)["input_schema"])
            if command == "skills list":
                return skills.list_(agents=args.get("target"), scope=args.get("scope"))
            if command == "skills uninstall":
                return skills.uninstall_(
                    args.get("skill"),
                    scope=args.get("scope", "project"),
                    force=args.get("force") is True,
                    yes=args.get("yes") is True,
                    agents=args.get("target"),
                )
            return skills.install_(
                args.get("skill"),
                scope=args.get("scope", "project"),
                force=args.get("force") is True,
                agents=args.get("target"),
            )
        if command not in {
            "session create",
            "session list",
            "session show",
            "session resume",
            "job get",
            "job wait",
            "job fetch",
            "job reconcile",
            "job cancel",
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
