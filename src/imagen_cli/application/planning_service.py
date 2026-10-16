"""Offline creation and approval planning; no provider dispatch."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import urlsplit

from imagen_cli.domain.enums import ApprovalState, Operation
from imagen_cli.domain.models import ApprovalContext, ExecutionPlan
from imagen_cli.domain.primitive import new_id
from imagen_cli.storage.repositories import Repositories

_REQUIRED_TARGET = {
    "profile_identity",
    "profile_revision",
    "endpoint_origin",
    "allowed_upload_targets",
}


def _utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class PlanningService:
    """Persist immutable, locally reviewable plans and their approval state."""

    def __init__(
        self, repositories: Repositories, *, clock: Callable[[], str] | None = None
    ) -> None:
        self.repositories = repositories
        self.clock = clock or (lambda: _utc(datetime.now(UTC)))

    def _now(self) -> datetime:
        value = self.clock()
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("clock must be timezone-aware")
        return result.astimezone(UTC)

    @staticmethod
    def _canonical(value: Mapping[str, Any]) -> str:
        return json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )

    def create_plan(
        self,
        *,
        session_id: str,
        target_snapshot: Mapping[str, Any],
        operation: str,
        model: str,
        normalized_parameters: Mapping[str, Any],
        prompt_snapshot: str,
        input_hashes: list[str] | tuple[str, ...],
        portable_context: Mapping[str, Any],
        capability_digest: str,
        context_loss: list[str] | tuple[str, ...] = (),
        ttl: timedelta = timedelta(hours=24),
        approval_required: bool = True,
    ) -> tuple[ExecutionPlan, ApprovalContext | None]:
        if ttl <= timedelta(0):
            raise ValueError("TTL must be positive")
        target = dict(target_snapshot)
        if not _REQUIRED_TARGET <= target.keys():
            raise ValueError(
                "target_snapshot requires identity, revision, endpoint and upload targets"
            )
        if any(
            key.casefold().replace("-", "_") in {"api_key", "access_token", "secret", "password"}
            for key in target
        ):
            raise ValueError("target_snapshot cannot contain credentials")
        if (
            not isinstance(target["profile_identity"], str)
            or not target["profile_identity"].strip()
        ):
            raise ValueError("target_snapshot requires profile identity")
        if type(target["profile_revision"]) is not int or target["profile_revision"] < 1:
            raise ValueError("target_snapshot requires positive profile revision")
        if not isinstance(target["allowed_upload_targets"], list):
            raise ValueError("target_snapshot upload targets must be a list")
        for address in [target["endpoint_origin"], *target["allowed_upload_targets"]]:
            if not isinstance(address, str):
                raise ValueError("target_snapshot URL must be text")
            parsed = urlsplit(address)
            if (
                parsed.scheme not in {"https", "http"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("target_snapshot requires explicit credential-free endpoint")
        session = self.repositories.get("sessions", session_id)
        if session is None or session.get("deleted_at") is not None:
            raise KeyError(session_id)
        now = self._now()
        created_at, expires_at = _utc(now), _utc(now + ttl)
        parent_turn_id = session.get("current_turn_id")
        digest_fields = {
            "session_id": session_id,
            "parent_turn_id": parent_turn_id,
            "expected_session_version": session.get("version", 0),
            "target_snapshot": target,
            "operation": Operation(operation).value,
            "model": model,
            "normalized_parameters": dict(normalized_parameters),
            "prompt_snapshot": prompt_snapshot,
            "input_hashes": list(input_hashes),
            "portable_context": dict(portable_context),
            "capability_digest": capability_digest,
            "context_loss": list(context_loss),
            "created_at": created_at,
            "expires_at": expires_at,
        }
        digest = hashlib.sha256(self._canonical(digest_fields).encode("utf-8")).hexdigest()
        plan = ExecutionPlan(
            id=new_id("pln"),
            session_id=session_id,
            parent_turn_id=parent_turn_id,
            expected_session_version=session.get("version", 0),
            target_snapshot=target,
            operation=Operation(operation),
            model=model,
            normalized_parameters=dict(normalized_parameters),
            prompt_snapshot=prompt_snapshot,
            input_hashes=list(input_hashes),
            portable_context=dict(portable_context),
            capability_digest=capability_digest,
            context_loss=list(context_loss),
            plan_digest=digest,
            created_at=created_at,
            expires_at=expires_at,
        )
        with self.repositories.transaction():
            self.repositories.insert("plans", plan.model_dump(mode="json"))
            approval = None
            if approval_required:
                approval = ApprovalContext(
                    id=new_id("apr"),
                    plan_id=plan.id,
                    plan_digest=digest,
                    state=ApprovalState.PENDING,
                    expires_at=expires_at,
                )
                self.repositories.insert("approvals", approval.model_dump(mode="json"))
        return plan, approval

    def _approval(self, plan_id: str) -> dict[str, Any]:
        rows = self.repositories.list("approvals", plan_id=plan_id, include_private=True)
        if not rows:
            raise ValueError("plan is not authorized")
        return rows[0]

    def _validated_plan(self, plan_id: str, digest: str) -> dict[str, Any]:
        plan = self.repositories.get("plans", plan_id, include_private=True)
        if plan is None or plan.get("plan_digest") != digest:
            raise ValueError("plan digest mismatch")
        fields = {key: value for key, value in plan.items() if key not in {"id", "plan_digest"}}
        computed = hashlib.sha256(self._canonical(fields).encode("utf-8")).hexdigest()
        if computed != digest:
            raise ValueError("persisted plan digest mismatch")
        return dict(plan)

    def approve(self, plan_id: str, digest: str) -> ApprovalContext:
        expired = False
        with self.repositories.transaction():
            self._validated_plan(plan_id, digest)
            approval = self._approval(plan_id)
            if datetime.fromisoformat(approval["expires_at"].replace("Z", "+00:00")) <= self._now():
                self.repositories.update(
                    "approvals", approval["id"], {"state": ApprovalState.EXPIRED.value}
                )
                expired = True
            elif approval["state"] == ApprovalState.APPROVED.value:
                return ApprovalContext.model_validate(approval)
            elif approval["state"] != ApprovalState.PENDING.value:
                raise ValueError("approval is not pending")
            else:
                updated = self.repositories.update(
                    "approvals",
                    approval["id"],
                    {"state": ApprovalState.APPROVED.value, "approved_at": _utc(self._now())},
                )
                return ApprovalContext.model_validate(updated)
        # Record expiry durably, then refuse outside the transaction.
        raise ValueError("approval expired" if expired else "approval is not pending")

    def execute_approved(
        self,
        plan_id: str,
        digest: str,
        turn: dict[str, Any],
        job: dict[str, Any],
        current_snapshot: Mapping[str, Any],
    ) -> str:
        with self.repositories.transaction():
            return self._execute_approved(plan_id, digest, turn, job, current_snapshot)

    def _execute_approved(
        self,
        plan_id: str,
        digest: str,
        turn: dict[str, Any],
        job: dict[str, Any],
        current_snapshot: Mapping[str, Any],
    ) -> str:
        plan = self._validated_plan(plan_id, digest)
        approval = self._approval(plan_id)
        if approval["state"] == ApprovalState.CONSUMED.value:
            return cast(str, approval["execution_job_id"])
        expected = {
            key: plan[key]
            for key in (
                "target_snapshot",
                "operation",
                "model",
                "normalized_parameters",
                "prompt_snapshot",
                "input_hashes",
                "portable_context",
                "capability_digest",
                "parent_turn_id",
                "expected_session_version",
            )
        }
        actual = {key: current_snapshot.get(key) for key in expected}
        if actual != expected:
            raise ValueError("current snapshot does not match plan")
        if approval["state"] != ApprovalState.APPROVED.value:
            raise ValueError("plan is not authorized")
        bindings = {
            "session_id": plan["session_id"],
            "parent_turn_id": plan["parent_turn_id"],
            "expected_session_version": plan["expected_session_version"],
            "instruction": plan["prompt_snapshot"],
            "operation": plan["operation"],
            "model": plan["model"],
            "parameters": plan["normalized_parameters"],
            "resolved_profile_snapshot": plan["target_snapshot"],
        }
        for key, value in bindings.items():
            if turn.get(key) != value:
                raise ValueError(f"turn {key} mismatch")
        if [item.get("sha256") for item in turn.get("input_manifest", [])] != plan["input_hashes"]:
            raise ValueError("turn input_hashes mismatch")
        if job.get("plan_id") != plan_id or job.get("turn_id") != turn.get("id"):
            raise ValueError("job binding mismatch")
        if job.get("requested_count", 1) != plan["normalized_parameters"].get("count", 1):
            raise ValueError("job count mismatch")
        for key, initial in {
            "state": "queued",
            "dispatch_state": "not_started",
            "remote_state": "not_submitted",
            "local_save_state": "not_started",
        }.items():
            if job.get(key, initial) != initial:
                raise ValueError("job must be unsubmitted")
        if datetime.fromisoformat(plan["expires_at"]) <= self._now():
            raise ValueError("plan expired")
        return cast(str, self.repositories.consume_approval(approval["id"], digest, turn, job))
