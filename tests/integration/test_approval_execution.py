"""Approvals bind exactly one job, expire, and never authorize a second create."""

from datetime import UTC, datetime, timedelta

import pytest

from imagen_cli.application.approval_service import ApprovalService
from imagen_cli.application.container import Container
from tests.integration.test_approved_execution import execute, service, setup


def test_approve_is_offline_and_idempotent(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        plan, _, _, _, adapter, _ = setup(container, png_bytes, approved=False)
        approvals = ApprovalService(container.repositories)
        assert approvals.state(plan.id) == "pending"
        approvals.approve(plan.id, plan.plan_digest)
        assert approvals.state(plan.id) == "approved"
        approvals.approve(plan.id, plan.plan_digest)
        assert approvals.state(plan.id) == "approved"
        assert adapter.requests == []


def test_expired_approval_is_recorded_and_refused(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        plan, approval, _, _, _, _ = setup(container, png_bytes, approved=False)
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        container.repositories.update("approvals", approval.id, {"expires_at": past})
        with pytest.raises(ValueError):
            ApprovalService(container.repositories).approve(plan.id, plan.plan_digest)
        assert ApprovalService(container.repositories).state(plan.id) == "expired"


def test_wrong_digest_and_invalidation_keep_the_approval_unusable(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        plan, _, _, _, _, _ = setup(container, png_bytes, approved=False)
        approvals = ApprovalService(container.repositories)
        with pytest.raises(ValueError):
            approvals.approve(plan.id, "b" * 64)
        assert approvals.state(plan.id) == "pending"
        assert approvals.invalidate(plan.id, "target changed") == "invalidated"
        with pytest.raises(ValueError):
            approvals.approve(plan.id, plan.plan_digest)


def test_consumed_approval_cannot_be_invalidated_or_reused(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        plan, _, validator, executor, adapter, _ = setup(container, png_bytes)
        api = service(container, validator, executor)
        first = execute(api, plan)
        assert first["status"] == "succeeded"
        assert ApprovalService(container.repositories).state(plan.id) == "consumed"
        with pytest.raises(ValueError):
            ApprovalService(container.repositories).invalidate(plan.id, "late")
        assert execute(api, plan) == first
        assert len(adapter.requests) == 1
