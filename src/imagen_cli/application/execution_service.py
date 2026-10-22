"""Internal approved-plan execution; consumed approvals are observation-only."""

import hashlib
from datetime import UTC, datetime
from typing import Any

from imagen_cli.application.planning_service import PlanningService
from imagen_cli.application.profile_service import ProfileService
from imagen_cli.domain.primitive import new_id
from imagen_cli.execution.foreground import ForegroundExecutor
from imagen_cli.providers.base import ValidatedRequest
from imagen_cli.storage.artifacts import MAX_BYTES, ArtifactStore, inspect_media
from imagen_cli.storage.repositories import Repositories


class ExecutionService:
    def __init__(
        self,
        repositories: Repositories,
        artifact_store: ArtifactStore,
        executor: ForegroundExecutor,
    ) -> None:
        if any(
            repo.connection is not repositories.connection
            for repo in (
                artifact_store.repositories,
                executor.repositories,
                executor.artifact_service.repositories,
                executor.artifact_service.artifact_store.repositories,
                executor.outcome_service.repositories,
            )
        ):
            raise ValueError("execution dependencies must share a connection")
        self.repositories = repositories
        self.artifact_store = artifact_store
        self.executor = executor
        self.planning = PlanningService(repositories)

    def execute_approved(
        self,
        plan_id: str,
        digest: str,
        *,
        model_revision: str,
        input_artifact_ids: tuple[str, ...] = (),
        mask_artifact_id: str | None = None,
    ) -> dict[str, Any]:
        if self.repositories.connection.in_transaction:
            raise ValueError("execution requires an independently committed transaction")
        with self.repositories.transaction():
            plan = self.planning._validated_plan(plan_id, digest)
            approval = self.planning._approval(plan_id)
            if approval.get("plan_digest") != digest:
                raise ValueError("approval digest mismatch")
            if approval["state"] == "consumed":
                return self._facts(approval["execution_job_id"])
            profile_row = self.repositories.get(
                "profiles", plan["target_snapshot"]["profile_identity"],
            )
            if profile_row is None:
                raise ValueError("profile is unavailable")
            profile = ProfileService().load(profile_row)
            context: dict[str, Any] = {"kind": "none"}
            if input_artifact_ids or mask_artifact_id is not None:
                context = {
                    "kind": "explicit_images",
                    "input_artifact_ids": list(input_artifact_ids),
                    "mask_artifact_id": mask_artifact_id,
                }
            sources = [("input", index, identity)
                       for index, identity in enumerate(input_artifact_ids)]
            if mask_artifact_id is not None:
                sources.append(("mask", 0, mask_artifact_id))
            contents, manifest = [], []
            for role, index, identity in sources:
                path = self.artifact_store.get(identity)
                if path is None:
                    raise ValueError("input artifact is unavailable")
                with path.open("rb") as stream:
                    content = stream.read(MAX_BYTES + 1)
                measured = inspect_media(content)
                row = self.repositories.get("artifacts", identity)
                if any(row.get(key) != value for key, value in measured.items()):
                    raise ValueError("input artifact metadata mismatch")
                contents.append(content)
                if sum(map(len, contents)) > 256 * 1024 * 1024:
                    raise ValueError("total input byte limit exceeded")
                manifest.append({
                    "artifact_id": identity, "role": role, "index": index,
                    "sha256": hashlib.sha256(content).hexdigest(),
                })
            session = self.repositories.get("sessions", plan["session_id"])
            if session is None or session.get("deleted_at") is not None:
                raise ValueError("session is unavailable")
            target = {
                "profile_identity": profile.id,
                "profile_revision": profile.revision,
                "endpoint_origin": profile.base_url,
                "allowed_upload_targets": profile.trust_policy.get(
                    "allowed_upload_targets", [profile.base_url],
                ),
                "provider": profile.provider,
                "family": profile.family,
                "model_revision": model_revision,
            }
            snapshot = {
                **plan,
                "target_snapshot": target,
                "capability_digest": plan["capability_digest"],
                "input_hashes": [item["sha256"] for item in manifest],
                "portable_context": context,
                "parent_turn_id": session.get("current_turn_id"),
                "expected_session_version": session["version"],
            }
            now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            turn_id, job_id = new_id("trn"), new_id("job")
            turn = {
                "id": turn_id, "session_id": plan["session_id"],
                "parent_turn_id": plan["parent_turn_id"],
                "expected_session_version": plan["expected_session_version"],
                "instruction": plan["prompt_snapshot"], "operation": plan["operation"],
                "model": plan["model"], "parameters": plan["normalized_parameters"],
                "input_manifest": manifest, "resolved_profile_snapshot": target,
                "job_id": None, "creation_state": "queued",
                "publication_state": "not_attempted", "created_at": now,
            }
            count = plan["normalized_parameters"].get("count", 1)
            if type(count) is not int or count < 1:
                raise ValueError("requested count must be positive")
            job = {
                "id": job_id, "turn_id": turn_id, "plan_id": plan_id,
                "state": "queued", "dispatch_state": "not_started",
                "remote_state": "not_submitted", "local_save_state": "not_started",
                "requested_count": count, "actual_count": 0, "accepted_maybe": False,
                "created_at": now, "updated_at": now,
            }
            self.planning.execute_approved(plan_id, digest, turn, job, snapshot)
            # Circular turn/job FKs require binding after both inserts, before commit.
            self.repositories.update("turns", turn_id, {"job_id": job_id})
            for item in manifest:
                self.repositories.insert("artifact_references", {
                    "session_id": plan["session_id"], "turn_id": turn_id,
                    **{key: value for key, value in item.items() if key != "sha256"},
                })
            request = ValidatedRequest(
                plan_id, job_id, profile.id, profile.revision, plan["operation"],
                plan["model"], model_revision, plan["prompt_snapshot"],
                plan["normalized_parameters"], plan["capability_digest"],
                authorized=True, dispatch_intent_recorded=False,
                inputs=tuple(contents[:-1] if mask_artifact_id is not None else contents),
                mask=contents[-1] if mask_artifact_id is not None else None,
            )
        self.executor.execute(request, requested_count=count)
        with self.repositories.transaction():
            return self._facts(job_id)

    def _facts(self, job_id: str) -> dict[str, Any]:
        job = self.repositories.get("jobs", job_id)
        if job is None:
            raise ValueError("consumed execution job is unavailable")
        turn = self.repositories.get("turns", job["turn_id"])
        if turn is None or turn.get("job_id") != job_id:
            raise ValueError("execution turn binding mismatch")
        artifacts = self.repositories.list("artifacts", producer_job_id=job_id, limit=1000)
        status = turn["creation_state"]
        if status in {"queued", "running"}:
            status = "unknown"
        if turn["publication_state"] == "conflict":
            status = "conflict"
        if job["remote_state"] == "succeeded" and job["local_save_state"] == "failed":
            status = "save_failed"
        return {
            "status": status,
            "exit_code": {"succeeded": 0, "unknown": 6, "conflict": 5,
                          "partial_success": 9}.get(status, 7),
            "job": job, "turn": turn, "artifacts": artifacts,
        }
