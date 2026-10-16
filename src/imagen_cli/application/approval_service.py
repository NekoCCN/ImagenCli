"""Approval lifecycle facade: pending, approved, consumed, expired, invalidated."""

from typing import Any

from imagen_cli.application.planning_service import PlanningService
from imagen_cli.storage.repositories import Repositories

__all__ = ["ApprovalService"]


class ApprovalService:
    def __init__(self, repositories: Repositories) -> None:
        self.repositories = repositories
        self.planning = PlanningService(repositories)

    def approve(self, plan_id: str, digest: str) -> dict[str, Any]:
        return self.planning.approve(plan_id, digest).model_dump()

    def state(self, plan_id: str) -> str:
        rows = self.repositories.list("approvals", plan_id=plan_id)
        if not rows:
            raise KeyError(plan_id)
        return str(rows[0]["state"])

    def invalidate(self, plan_id: str, reason: str) -> str:
        """Only an unconsumed approval may be invalidated; execution rights never return."""
        rows = self.repositories.list("approvals", plan_id=plan_id)
        if not rows:
            raise KeyError(plan_id)
        approval = rows[0]
        if approval["state"] == "consumed":
            raise ValueError("consumed approval is immutable")
        self.repositories.update(
            "approvals", approval["id"], {"state": "invalidated"},
        )
        return "invalidated"
