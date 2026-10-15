"""Local plan review and explicit digest approval; approval never dispatches."""

from typing import Any

from imagen_cli.application.planning_service import PlanningService
from imagen_cli.storage.repositories import Repositories


def execute(
    repositories: Repositories, command: str, plan_id: str, digest: str | None = None,
) -> tuple[str, dict[str, Any]]:
    row = repositories.get("plans", plan_id, include_private=True)
    if row is None:
        return "not_found", {}
    service = PlanningService(repositories)
    plan = service._validated_plan(plan_id, row["plan_digest"])
    if command == "plan show":
        return "succeeded", {"plan": plan}
    if digest is None:
        raise ValueError("digest required")
    approval = service.approve(plan_id, digest)
    return "approved", {"approval": approval.model_dump(mode="json")}
