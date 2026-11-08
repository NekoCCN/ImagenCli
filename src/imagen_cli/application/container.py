"""Dependency composition root; offline commands have no provider or storage side effects."""

import difflib
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

_KINDS = {"operator": ("operator", "算子"), "preset": ("preset", "预设")}


def _unknown_identifier(value: str, options: Any, kind: str, command: str) -> str:
    """Name the miss, suggest a near match, and point at the full list."""
    english, chinese = _KINDS[kind]
    matches = difflib.get_close_matches(value, sorted(map(str, options)), n=3)
    if matches:
        suggestion_en = f" Did you mean {', '.join(matches)}?"
        suggestion_zh = f"是不是 {', '.join(matches)}？"
    else:
        suggestion_en = suggestion_zh = ""
    return str(
        i18n.t(
            f"Unknown {english} '{value}'.{suggestion_en} Run `{command}` to list them.",
            f"未知{chinese} '{value}'。{suggestion_zh}运行 `{command}` 查看列表。",
        )
    )


def _op_spec(item: Any) -> str:
    """A runnable `name:key=value` spec built from the operator's defaults."""
    pairs = []
    for parameter in item.basic_parameters:
        value = parameter.default
        if isinstance(value, bool):
            value = "true" if value else "false"
        elif isinstance(value, str) and (not value or "," in value):
            value = f'"{value}"'
        pairs.append(f"{parameter.name}={value}")
    op = str(item.default_op)
    return op + (":" + ",".join(pairs) if pairs else "")


def _signature_parameters(op: str) -> list[dict[str, Any]]:
    """Fallback for operators the curated catalog does not describe yet."""
    import inspect

    from imagen_cli.application import media_ops

    operation = media_ops.OPS.get(op)
    if operation is None:
        return []
    try:
        parameters = list(inspect.signature(operation).parameters.values())[1:]
    except (TypeError, ValueError):
        return []
    kinds = {int: "int", float: "float", bool: "bool", str: "str"}
    rows = []
    for parameter in parameters:
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            continue
        annotation = parameter.annotation
        missing = parameter.default is inspect.Parameter.empty
        rows.append(
            {
                "name": parameter.name,
                "label": parameter.name,
                "type": kinds.get(annotation, str(annotation).strip("'\""))
                if annotation is not inspect.Parameter.empty
                else "value",
                "default": None if missing else parameter.default,
                "min": None,
                "max": None,
                "choices": None,
                "description": "",
                "advanced": False,
                "required": missing,
            }
        )
    return rows


def _operator_details(catalog: Any, op: str) -> dict[str, Any]:
    """Intro plus the parameters a caller types after `--op`."""
    details: dict[str, Any] = dict(catalog.localized_intro(op) or {})
    details["op"] = op
    # The operator page is the hub: name the recipes that use it, and the
    # single-operator presets whose tuned parameters differ from the defaults.
    details["presets"] = [
        key
        for key, pipeline in catalog.PRESET_PIPELINES.items()
        if key != op and len(pipeline) > 1 and any(step.get("op") == op for step in pipeline)
    ][:6]
    item = catalog.get_operator_item(op)
    defaults = item.default_params if item is not None else None
    variants = []
    for key, pipeline in catalog.PRESET_PIPELINES.items():
        if key == op or len(pipeline) != 1:
            continue
        step = pipeline[0]
        if step.get("op") != op:
            continue
        params = step.get("params", {})
        if defaults is not None and params == defaults:
            continue
        variants.append({"preset": key, "params": params})
    if variants:
        details["variants"] = variants
    if item is None:
        details.setdefault("title", op)
        details["parameters"] = _signature_parameters(op)
        details.setdefault("example", f"imagen transform ARTIFACT --op '{op}'")
        return details
    shown = catalog.localized_operator(item)
    details.setdefault("title", shown.name)
    details.setdefault("summary", shown.description)
    details["category"] = shown.category_label
    details["parameters"] = [parameter.as_dict() for parameter in shown.parameters]
    details["example"] = f"imagen transform ARTIFACT --op '{_op_spec(shown)}'"
    return details


@dataclass
class Container:
    """Application boundary intentionally keeps provider dependencies optional."""

    workspace: str | None = None
    options: SharedOptions = field(default_factory=SharedOptions)
    progress: Any = None

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
        if command == "update":
            from imagen_cli.application import update_service

            if isinstance(args, dict) and args.get("install") is True:
                return update_service.upgrade()
            return update_service.check()
        if command == "language":
            if "language" in args:
                saved = i18n.save_language(str(args["language"]))
                return "succeeded", {"language": saved, "saved": True}
            return "succeeded", {"language": i18n.language(), "saved": False}
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
                progress=self.progress,
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
                timeout=args.get("timeout"),
                budget_approval=args.get("budget_approval"),
                asynchronous=self.options.async_,
                progress=self.progress,
            )
        if command.startswith("remote "):
            from jsonschema import validate

            from imagen_cli.application import remote_service
            from imagen_cli.cli.registry import discover

            validate(args, discover(command)["input_schema"])
            try:
                if command == "remote list":
                    return "succeeded", {"remotes": remote_service.list_()}
                if command == "remote add":
                    return "succeeded", remote_service.add(
                        args["name"], args["endpoint"], args["credential_ref"],
                        gateway_credential_ref=args.get("gateway_credential_ref"),
                        private_store=self.private_state_store,
                    )
                if command == "remote status":
                    return "succeeded", remote_service.status(
                        args["name"], private_store=self.private_state_store,
                    )
                if command == "remote jobs list":
                    return "succeeded", {
                        "jobs": remote_service.jobs_list(
                            self.repositories, args.get("provider"),
                        ),
                    }
                if command.startswith("remote job "):
                    action = command.removeprefix("remote job ")
                    if action == "get":
                        return "succeeded", remote_service.job_get(
                            self.repositories, args["job"],
                            private_store=self.private_state_store,
                        )
                    if action == "resume":
                        return remote_service.job_resume(
                            self.repositories, self.artifact_store, args["job"],
                            private_store=self.private_state_store,
                        )
                    if action == "retry-interrupted":
                        return remote_service.job_retry_interrupted(
                            self.repositories, self.artifact_store, args["job"],
                            private_store=self.private_state_store,
                        )
                    return remote_service.job_cancel(
                        self.repositories, args["job"],
                        private_store=self.private_state_store,
                    )
                remote_service.remove(args["name"])
                return "succeeded", {"removed": args["name"]}
            except remote_service.RemoteError as error:
                return error.status, {"error": {"type": error.status, "detail": str(error)}}
            except (ValueError, OSError) as error:
                return "validation_error", {"error": {
                    "type": "validation_error", "detail": str(error),
                }}
        if command.startswith("deploy "):
            from jsonschema import validate

            from imagen_cli.application import deploy_service
            from imagen_cli.cli.registry import discover

            validate(args, discover(command)["input_schema"])
            try:
                if command == "deploy status":
                    return deploy_service.status(
                        args["vendor"], name=args.get("name"),
                        salad_key_ref=args.get("salad_key_ref"),
                        worker_token_ref=args.get("worker_token_ref"),
                        salad_org=args.get("salad_org"),
                        salad_project=args.get("salad_project"),
                        private_store=self.private_state_store,
                    )
                if command == "deploy update":
                    return deploy_service.update(
                        args["vendor"], image=args["image"],
                        name=args.get("name"), rename=args.get("rename"),
                        set_env=args.get("set_env"),
                        accept=args.get("accept") is True,
                        yes=args.get("yes") is True,
                        salad_key_ref=args.get("salad_key_ref"),
                        worker_token_ref=args.get("worker_token_ref"),
                        salad_org=args.get("salad_org"),
                        salad_project=args.get("salad_project"),
                        modal_token_id_ref=args.get("modal_token_id_ref"),
                        modal_token_secret_ref=args.get("modal_token_secret_ref"),
                        modal_gpu=args.get("modal_gpu"),
                        modal_memory_mb=args.get("modal_memory_mb"),
                        modal_cpu=args.get("modal_cpu"),
                        max_seconds=args.get("max_seconds", 900),
                        private_store=self.private_state_store,
                    )
                if command == "deploy build":
                    return deploy_service.build(
                        tag=args["tag"], image=args["image"],
                        push=args.get("push") is True,
                        target=args.get("image_target", "worker"),
                        salad_queue_worker_url_ref=args.get(
                            "salad_queue_worker_url_ref"),
                        base_image=args.get("base_image"),
                        registry_username_ref=args.get("registry_user_ref"),
                        registry_password_ref=args.get("registry_password_ref"),
                        accept=args.get("accept") is True,
                        private_store=self.private_state_store,
                    )
                if command == "deploy remove":
                    return deploy_service.remove(
                        args["vendor"], name=args.get("name"),
                        accept=args.get("accept") is True,
                        yes=args.get("yes") is True,
                        salad_key_ref=args.get("salad_key_ref"),
                        salad_org=args.get("salad_org"),
                        salad_project=args.get("salad_project"),
                        modal_token_id_ref=args.get("modal_token_id_ref"),
                        modal_token_secret_ref=args.get("modal_token_secret_ref"),
                        private_store=self.private_state_store,
                    )
                return deploy_service.set_secret(
                    self.private_state_store, args["name"], args["key_file"],
                )
            except (ValueError, OSError) as error:
                return "validation_error", {"error": {
                    "type": "validation_error", "detail": str(error),
                }}
        if command.startswith("watermark "):
            from jsonschema import validate

            from imagen_cli.application import (
                remote_service,
                watermark_resample,
                watermark_service,
            )
            from imagen_cli.application.postprocess_service import BudgetApprovalRequired
            from imagen_cli.cli.registry import discover

            validate(args, discover(command)["input_schema"])
            failures = {
                "missing": "unsupported_capability",
                "incompatible_worker": "unsupported_capability",
                "incompatible_raiw": "unsupported_capability",
            }

            def failed(error: Any) -> tuple[str, dict[str, Any]]:
                status = str(failures.get(error.status, error.status))
                return status, {"error": {"type": status, "detail": str(error)},
                                **error.data}

            if command == "watermark status":
                return "succeeded", watermark_service.status()
            if command == "watermark setup":
                try:
                    return "succeeded", watermark_service.setup(
                        args.get("accept") is True, args.get("extras"),
                    )
                except (ValueError, watermark_service.WatermarkError) as error:
                    if isinstance(error, watermark_service.WatermarkError):
                        return failed(error)
                    return "validation_error", {"error": {
                        "type": "validation_error", "detail": str(error),
                    }}
            if command == "watermark update":
                try:
                    return "succeeded", watermark_service.update(
                        args.get("accept") is True, args.get("to"), args.get("extras"),
                    )
                except (ValueError, watermark_service.WatermarkError) as error:
                    if isinstance(error, watermark_service.WatermarkError):
                        return failed(error)
                    return "validation_error", {"error": {
                        "type": "validation_error", "detail": str(error),
                    }}
            if command in {"watermark batch", "watermark video batch"}:
                try:
                    if command == "watermark batch":
                        return "succeeded", watermark_service.batch_directory(
                            args["directory"], to=args.get("to"),
                            mode=args.get("mode", "all"),
                            pipeline=args.get("pipeline", "sdxl-zimage"),
                            backend=args.get("backend", "auto"),
                            sensitivity=args.get("sensitivity", "auto"),
                            force=args.get("force") is True,
                            accept_license=args.get("accept_license") is True,
                            options={
                                "strength": args.get("strength"),
                                "seed": args.get("seed"),
                                "humanize": args.get("humanize"),
                                "unsharp": args.get("unsharp"),
                                "tile": args.get("tile") is True,
                                "tile_size": args.get("tile_size", 1024),
                                "tile_overlap": args.get("tile_overlap"),
                                "cpu_offload": args.get("cpu_offload") is True,
                                "max_resolution": args.get("max_resolution"),
                                "adaptive_polish": args.get("adaptive_polish"),
                                "text_manifest": args.get("text_manifest"),
                                "fidelity_anchor": args.get("fidelity_anchor"),
                                "vendor": args.get("vendor"),
                            },
                            timeout=args.get("max_seconds") or 3600,
                        )
                    return "succeeded", watermark_service.video_batch_directory(
                        args["directory"], to=args.get("to"),
                        mode=args.get("mode", "all"),
                        options={
                            "mark": args.get("mark"),
                            "backend": args.get("backend"),
                            "include_invisible": args.get("include_invisible"),
                            "noise_std": args.get("noise_std"),
                            "long_side": args.get("long_side"),
                            "fps": args.get("fps"),
                            "batch_size": args.get("batch_size"),
                            "seed": args.get("seed"),
                        },
                        timeout=args.get("max_seconds") or 7200,
                    )
                except BudgetApprovalRequired as error:
                    return "approval_required", {
                        "budget": error.report,
                        "error": {"type": "approval_required", "detail": str(error)},
                    }
                except (ValueError, watermark_service.WatermarkError) as error:
                    if isinstance(error, watermark_service.WatermarkError):
                        return failed(error)
                    return "validation_error", {"error": {
                        "type": "validation_error", "detail": str(error),
                    }}
            if command == "watermark resample":
                if args.get("remote"):
                    return "validation_error", {"error": {
                        "type": "validation_error",
                        "detail": "watermark resample runs locally; it does not accept --remote",
                    }}
                try:
                    return watermark_resample.run(
                        self.repositories, self.artifact_store, args["artifact"],
                        session=args.get("session"),
                        model=args.get("model", "realesrgan-x2plus"),
                        factor=args.get("factor"),
                        backend=args.get("backend"),
                        tile=args.get("tile", 256),
                        overlap=args.get("overlap", 16),
                        budget_approval=args.get("budget_approval"),
                        progress=self.progress,
                    )
                except BudgetApprovalRequired as error:
                    return "approval_required", {
                        "budget": error.report,
                        "error": {"type": "approval_required", "detail": str(error)},
                    }
                except watermark_service.WatermarkError as error:
                    return failed(error)
                except ValueError as error:
                    return "validation_error", {"error": {
                        "type": "validation_error", "detail": str(error),
                    }}
            parts = command.split()
            operation = ("watermark.video." + parts[2] if parts[1] == "video"
                         else "watermark." + parts[1])
            if operation == "watermark.metadata" and args.get("remove") is not True:
                operation = "watermark.identify"
            if operation == "watermark.video.metadata" and args.get("remove") is not True:
                operation = "watermark.video.identify"
            tuning = {
                "sensitivity": args.get("sensitivity"),
                "mark": args.get("mark"),
                "inpaint_method": args.get("inpaint_method"),
                "dilate": args.get("dilate"),
                "tile_overlap": args.get("tile_overlap"),
                "seed": args.get("seed"),
                "humanize": args.get("humanize"),
                "unsharp": args.get("unsharp"),
                "controlnet_conditioning_scale": args.get("controlnet_conditioning_scale"),
                "adaptive_polish": args.get("adaptive_polish"),
                "text_manifest": args.get("text_manifest"),
                "fidelity_anchor": args.get("fidelity_anchor"),
                "vendor": args.get("vendor"),
                "classify_source": args.get("source"),
                "backend": args.get("backend"),
                "include_invisible": args.get("include_invisible"),
                "noise_std": args.get("noise_std"),
                "long_side": args.get("long_side"),
                "fps": args.get("fps"),
                "batch_size": args.get("batch_size"),
                "device": args.get("device"),
                "model": args.get("model"),
            }
            remote_name = args.get("remote")
            if remote_name:
                remote_params = {
                    "pipeline": args.get("pipeline"),
                    "backend": args.get("backend"),
                    "sensitivity": args.get("sensitivity"),
                    "mark": args.get("mark"),
                    "tile": args.get("tile") is True,
                    "tile_size": args.get("tile_size", 1024),
                    "tile_overlap": args.get("tile_overlap"),
                    "cpu_offload": args.get("cpu_offload") is True,
                    "max_resolution": args.get("max_resolution"),
                    "force": args.get("force") is True,
                    "strength": args.get("strength"),
                    "seed": args.get("seed"),
                    "humanize": args.get("humanize"),
                    "unsharp": args.get("unsharp"),
                    "controlnet_conditioning_scale": args.get(
                        "controlnet_conditioning_scale"),
                    "adaptive_polish": args.get("adaptive_polish"),
                    "text_manifest": args.get("text_manifest"),
                    "fidelity_anchor": args.get("fidelity_anchor"),
                    "vendor": args.get("vendor"),
                    "regions": args.get("regions"),
                    "inpaint_method": args.get("inpaint_method"),
                    "dilate": args.get("dilate"),
                    "source": args.get("source"),
                }
                try:
                    return remote_service.run_operation(
                        self.repositories, self.artifact_store, args["artifact"],
                        remote_name, operation,
                        accept_remote=args.get("accept_remote"),
                        session=args.get("session"),
                        pipeline=args.get("pipeline"),
                        params={key: value for key, value in remote_params.items()
                                if value is not None},
                        max_seconds=args.get("max_seconds"),
                        accept_license=args.get("accept_license") is True,
                        memory_mb=args.get("memory_mb"),
                        budget_approval=args.get("budget_approval"),
                        private_store=self.private_state_store,
                    )
                except BudgetApprovalRequired as error:
                    return "approval_required", {
                        "budget": error.report,
                        "error": {"type": "approval_required", "detail": str(error)},
                    }
                except (ValueError, watermark_service.WatermarkError) as error:
                    if isinstance(error, watermark_service.WatermarkError):
                        return failed(error)
                    return "validation_error", {"error": {
                        "type": "validation_error", "detail": str(error),
                    }}
            try:
                return watermark_service.run_operation(
                    self.repositories, self.artifact_store, args["artifact"], operation,
                    session=args.get("session"),
                    pipeline=args.get("pipeline"),
                    visible_backend=args.get("backend"),
                    tile=args.get("tile") is True,
                    tile_size=args.get("tile_size", 1024),
                    cpu_offload=args.get("cpu_offload") is True,
                    max_resolution=args.get("max_resolution"),
                    force=args.get("force") is True,
                    accept_license=args.get("accept_license") is True,
                    strength=args.get("strength"),
                    timeout=args.get("max_seconds")
                    or (3600 if operation.startswith("watermark.video.")
                        else 900 if operation in {"watermark.invisible", "watermark.all"}
                        else self.options.timeout),
                    memory_mb=args.get("memory_mb"),
                    budget_approval=args.get("budget_approval"),
                    regions=args.get("regions"),
                    options=tuning,
                )
            except BudgetApprovalRequired as error:
                return "approval_required", {
                    "budget": error.report,
                    "error": {"type": "approval_required", "detail": str(error)},
                }
            except watermark_service.WatermarkError as error:
                return failed(error)
        if command.startswith("matting "):
            from jsonschema import validate

            from imagen_cli.application import matting

            validate(args, discover(command)["input_schema"])
            if command == "matting run":
                import imagen_cli.application.matting_api as matting_api
                from imagen_cli.cli.commands import postprocess

                model = str(args.get("model", "birefnet-lite"))
                api_model = "/" in model
                if api_model and args.get("remote"):
                    return "validation_error", {"error": {
                        "type": "validation_error",
                        "detail": i18n.t(
                            "An API model cannot be combined with --remote; pick one path.",
                            "API 模型不能与 --remote 同时使用，请只选一条路径。",
                        ),
                    }}
                if api_model and args.get("backend") not in (None, "auto"):
                    return "validation_error", {"error": {
                        "type": "validation_error",
                        "detail": i18n.t(
                            "--backend does not apply to API models: the vendor runs "
                            "the model. Drop the flag, or pick a local model.",
                            "--backend 对 API 模型无效：算力由厂商提供。"
                            "去掉该参数，或改用本地模型。",
                        ),
                    }}
                if api_model:
                    return matting_api.run(
                        self.repositories, self.artifact_store, args["artifact"],
                        model=model,
                        session=args.get("session"),
                        resolution=args.get("resolution"),
                        mask_blur=args.get("mask_blur"),
                        mask_offset=args.get("mask_offset"),
                        refine_fg=args.get("refine_fg"),
                        accept_api=args.get("accept_api"),
                        key_ref=str(args.get("replicate_key_ref")
                                   or "env:REPLICATE_API_TOKEN"),
                        timeout=float(args.get("timeout") or 900),
                    )
                matting_params: dict[str, Any] = {
                    key: args[key] for key in
                    ("model", "backend", "tile", "overlap", "feather", "edge_shift",
                     "accept", "precision")
                    if key in args
                }
                remote_name = args.get("remote")
                if remote_name:
                    from imagen_cli.application import remote_service

                    remote_params = {
                        key: matting_params[key]
                        for key in ("model", "backend", "tile", "overlap",
                                    "feather", "edge_shift", "precision")
                        if key in matting_params
                    }
                    try:
                        return remote_service.run_operation(
                            self.repositories, self.artifact_store, args["artifact"],
                            remote_name, "matte",
                            accept_remote=args.get("accept_remote"),
                            session=args.get("session"),
                            params=remote_params,
                            max_seconds=args.get("max_seconds"),
                            private_store=self.private_state_store,
                        )
                    except (ValueError, remote_service.RemoteError) as error:
                        status = getattr(error, "status", "validation_error")
                        return status, {"error": {
                            "type": status, "detail": str(error),
                        }}
                return postprocess.transform_(
                    self.repositories, self.artifact_store, args["artifact"],
                    model_params=matting_params, op_name="matte",
                    session=args.get("session"), timeout=args.get("timeout"),
                    budget_approval=args.get("budget_approval"),
                    asynchronous=self.options.async_,
                    progress=self.progress,
                )
            if command == "matting models":
                return "succeeded", matting.catalog(verify=args.get("verify") is True)
            if command == "matting doctor":
                report = matting.doctor(args.get("model"), backend=args.get("backend", "auto"))
                return ("succeeded" if report["status"] == "ready" else "unsupported_capability",
                        report)
            if command == "matting install":
                return "succeeded", matting.install(
                    args.get("model"),
                    backend=args.get("backend", "cpu"),
                    accept=args.get("accept") is True,
                )
            return "succeeded", matting.fetch(
                args["model"], accept=args.get("accept") is True,
            )
        if command.startswith("superres "):
            from jsonschema import validate

            from imagen_cli.application import superres

            validate(args, discover(command)["input_schema"])
            if command == "superres run":
                from imagen_cli.cli.commands import postprocess

                params: dict[str, Any] = {
                    key: args[key] for key in ("model", "backend", "tile", "overlap", "accept")
                    if key in args
                }
                return postprocess.transform_(
                    self.repositories, self.artifact_store, args["artifact"],
                    model_params=params,
                    session=args.get("session"), timeout=args.get("timeout"),
                    budget_approval=args.get("budget_approval"),
                    asynchronous=self.options.async_,
                    progress=self.progress,
                )
            if command == "superres models":
                return "succeeded", superres.catalog(verify=args.get("verify") is True)
            if command == "superres doctor":
                report = superres.doctor(args.get("model"), backend=args.get("backend", "auto"))
                return ("succeeded" if report["status"] == "ready" else "unsupported_capability",
                        report)
            if command == "superres install":
                return "succeeded", superres.install(
                    args.get("model"),
                    backend=args.get("backend", "cpu"),
                    accept=args.get("accept") is True,
                )
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
            if target_op and target_op not in media_ops.OPS:
                raise ValueError(
                    _unknown_identifier(target_op, media_ops.OPS, "operator", "imagen ops")
                )
            if target_op or target_preset or verbose:
                details: dict[str, Any] = {}
                if target_op:
                    details[target_op] = _operator_details(operator_catalog, target_op)
                elif target_preset:
                    match_key = next(
                        (
                            k
                            for k in operator_catalog.PRESET_PIPELINES
                            if target_preset.lower() in k.lower()
                        ),
                        None,
                    )
                    if match_key is None:
                        raise ValueError(
                            _unknown_identifier(
                                target_preset,
                                list(operator_catalog.PRESET_PIPELINES),
                                "preset",
                                "imagen ops",
                            )
                        )
                    details[match_key] = {
                        "pipeline": operator_catalog.PRESET_PIPELINES[match_key],
                        "intro": operator_catalog.localized_intro(match_key) or {},
                        "steps": [
                            _operator_details(operator_catalog, step["op"])
                            for step in operator_catalog.PRESET_PIPELINES[match_key]
                        ],
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
                parameters = dict(args)
                key_file = parameters.pop("key_file", None)
                if key_file:
                    # Store the secret first, then point the profile at it; the value
                    # never enters arguments or output.
                    if self.private_state_store is None:
                        raise ValueError("private credential storage is unavailable")
                    parameters["credential_ref"] = self.private_state_store.write_credential(
                        str(parameters["profile"]), profile.read_key(key_file)
                    )
                profile_data = profile.build_set(parameters)
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
            "session import-image",
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
            if command == "session import-image":
                return history.import_image_(
                    self.repositories,
                    self.artifact_store,
                    args["session"],
                    args["path"],
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
        if command in {"asset show", "asset delete", "asset remove", "asset save"}:
            from jsonschema import validate

            from imagen_cli.cli.commands import asset

            validate(args, discover(command)["input_schema"])
            if command == "asset save":
                return asset.save_(
                    self.repositories,
                    self.artifact_store,
                    args["artifact"],
                    args["path"],
                )
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
            if command == "skills update":
                return skills.update_(
                    args.get("skill"),
                    scope=args.get("scope", "project"),
                    force=args.get("force") is True,
                    agents=args.get("target"),
                )
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
            if "intent" in args and "intent_file" in args:
                raise ValueError("use intent or intent_file, not both")
            intent = args.get("intent") or ""
            if not intent and "intent_file" in args:
                intent = read_text(args["intent_file"])
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
            return job.wait_(artifacts, args["job_id"], timeout=self.options.timeout,
                             progress=self.progress)
        if command in {"job fetch", "job reconcile"}:
            from imagen_cli.application.reconcile import recover_inline

            try:
                recover_inline(artifacts, self.private_state_store, args["job_id"])
            except (OSError, ValueError):
                # Keep the durable remote fact and report remaining local save failures.
                pass
        handler = {"job get": job.get_, "job fetch": job.fetch_, "job reconcile": job.reconcile_}
        return handler[command](artifacts, args["job_id"])
