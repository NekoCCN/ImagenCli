"""Injectable creation handlers; production approval/planning is not provided here."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from imagen_cli.application.profile_service import ProfileService
from imagen_cli.application.session_service import SessionService
from imagen_cli.capabilities.validator import CapabilityValidator
from imagen_cli.cli.input_readers import read_prompt
from imagen_cli.domain.contracts import CreationInput
from imagen_cli.domain.models import ProviderProfile
from imagen_cli.providers.base import ValidatedRequest


@dataclass(frozen=True)
class CreationSnapshot:
    digest: str
    profile_identity: str
    prompt: str = field(repr=False)
    input_hashes: tuple[str, ...]
    disclosure: str = field(repr=False)
    inputs: tuple[bytes, ...] = field(repr=False)


@dataclass(frozen=True)
class AuthorizedPlan:
    plan_id: str
    digest: str


class Executor(Protocol):
    def execute(
        self, request: ValidatedRequest, requested_count: int | None = None
    ) -> dict[str, Any]: ...


class CreateHandler:
    def __init__(
        self,
        sessions: SessionService,
        profiles: ProfileService,
        capabilities: CapabilityValidator,
        executor: Executor,
        *,
        planning_gate: Callable[[CreationSnapshot], AuthorizedPlan | None] | None = None,
    ) -> None:
        self.sessions = sessions
        self.profiles = profiles
        self.capabilities = capabilities
        self.executor = executor
        self.planning_gate = planning_gate

    def handle(
        self,
        creation: CreationInput,
        *,
        profile: ProviderProfile,
        model_revision: str,
        asynchronous: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        if asynchronous:
            return "unsupported_capability", {}
        try:
            creation = CreationInput.model_validate(creation.model_dump(exclude_none=True))
            profile = self.profiles.load(profile.model_dump())
            prompt = read_prompt(prompt=creation.prompt)
            if creation.profile is not None and creation.profile != profile.id:
                return "validation_error", {}
            model = creation.model or profile.model_default
            parameters = dict(creation.provider_params)
            if "count" in parameters or "size" in parameters:
                return "validation_error", {}
            parameters["count"] = creation.count
            if creation.size is not None:
                parameters["size"] = creation.size
            try:
                decision = self.capabilities.validate(
                    profile.provider,
                    profile.family,
                    model,
                    model_revision,
                    creation.operation,
                    parameters,
                )
            except ValueError:
                return "unsupported_capability", {}
            if not decision.supported:
                return "unsupported_capability", {}
            if creation.references or creation.retry_of_job_id:
                return "unsupported_capability", {}
            session = self.sessions.resume(creation.session)["session"]
            contents: list[bytes] = []
            references: dict[str, list[str]] = {}
            sources = [("input", item) for item in creation.inputs]
            if creation.mask is not None:
                sources.append(("mask", creation.mask))
            for role, source in sources:
                if source.startswith("artifact:"):
                    identity = source.removeprefix("artifact:")
                    path = self.sessions.artifact_store.get(identity)
                    if path is None:
                        raise ValueError("selected artifact unavailable")
                    references.setdefault(role, []).append(identity)
                else:
                    path = Path(source.removeprefix("file:"))
                with path.open("rb") as stream:
                    content = stream.read(64 * 1024 * 1024 + 1)
                if not content or len(content) > 64 * 1024 * 1024:
                    raise ValueError("input byte limit exceeded")
                contents.append(content)
            if sum(map(len, contents)) > 256 * 1024 * 1024:
                raise ValueError("total input byte limit exceeded")
            hashes = tuple(hashlib.sha256(item).hexdigest() for item in contents)
            disclosure = json.dumps(
                {
                    "session": session.id,
                    "version": session.version,
                    "parent": session.current_turn_id,
                    "profile": profile.model_dump(mode="json"),
                    "model": model,
                    "revision": model_revision,
                    "operation": creation.operation,
                    "prompt": prompt,
                    "parameters": parameters,
                    "input_hashes": hashes,
                    "input_sources": [source for _, source in sources],
                    "capability": decision.digest,
                },
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            )
            snapshot = CreationSnapshot(
                hashlib.sha256(disclosure.encode()).hexdigest(),
                profile.id,
                prompt,
                hashes,
                disclosure,
                tuple(contents),
            )
            if self.planning_gate is None:
                return "approval_required", {}
            approved = self.planning_gate(snapshot)
            if not isinstance(approved, AuthorizedPlan) or approved.digest != snapshot.digest:
                return "approval_required", {}
            # shortcut: approval persistence is injected, integrate T059/T060 before CLI wiring.
            with self.sessions.repositories.transaction():
                current = self.sessions.resume(session.id)["session"]
                if (
                    current.version != session.version
                    or current.current_turn_id != session.current_turn_id
                ):
                    return "conflict", {}
                turn = self.sessions.create_turn(
                    session.id,
                    instruction=prompt,
                    operation=creation.operation,
                    resolved_profile_snapshot=profile.model_dump(mode="json"),
                    model=model,
                    parameters=parameters,
                    requested_count=creation.count,
                    parent_turn_id=session.current_turn_id,
                    references=references,
                )
            mask = contents[-1] if creation.mask is not None else None
            inputs = tuple(contents[:-1] if mask is not None else contents)
            request = ValidatedRequest(
                approved.plan_id,
                turn.job_id,
                profile.id,
                profile.revision,
                creation.operation,
                model,
                model_revision,
                prompt,
                parameters,
                decision.digest,
                authorized=True,
                dispatch_intent_recorded=False,
                inputs=inputs,
                mask=mask,
            )
        except (ValueError, KeyError, OSError):
            return "validation_error", {}
        result = self.executor.execute(request, requested_count=creation.count)
        return result["status"], {**result, "job_id": turn.job_id, "turn_id": turn.id}
