"""Hard process exits must never replay a consumed approval."""

import multiprocessing
import os
from pathlib import Path

import pytest

from imagen_cli.application.container import Container
from tests.integration.test_approved_execution import execute, service, setup


def _crash_child(workspace, png_bytes, plan_id, boundary):
    from imagen_cli.application.artifact_service import ArtifactService
    from imagen_cli.application.outcome_service import OutcomeService
    from imagen_cli.capabilities.catalog import CapabilityCatalog
    from imagen_cli.capabilities.validator import CapabilityValidator
    from imagen_cli.domain.models import ExecutionPlan
    from imagen_cli.execution.foreground import ForegroundExecutor
    from tests.integration.test_approved_execution import OfflineAdapter
    from tests.unit.test_capabilities import capability

    with Container(workspace=workspace) as container:
        plan = ExecutionPlan.model_validate(container.repositories.get("plans", plan_id))
        validator = CapabilityValidator(CapabilityCatalog([capability(
            parameter_schema={"type": "object", "properties": {"count": {"const": 1}},
                              "additionalProperties": False},
            continuation_strategy="none",
        )]))

        def observe(request):
            if boundary == "intent":
                os._exit(73)
            with (Path(workspace) / "creates.txt").open("a") as stream:
                stream.write(request.job_id + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            if boundary == "remote":
                os._exit(73)

        artifacts = ArtifactService(container.repositories, container.artifact_store)
        if boundary == "response":
            artifacts.save = lambda *a, **k: os._exit(73)
        executor = ForegroundExecutor(
            container.repositories, OfflineAdapter(png_bytes, observe=observe), artifacts,
            OutcomeService(container.repositories), container.private_state_store,
        )
        execute(service(container, validator, executor), plan)
        raise AssertionError("crash boundary was not reached")


@pytest.mark.parametrize("boundary", ["intent", "remote", "response"])
def test_restart_after_hard_exit_observes_without_creating(tmp_path, png_bytes, boundary):
    workspace = str(tmp_path / "workspace")
    with Container(workspace=workspace) as container:
        plan, approval, validator, _, _, _ = setup(container, png_bytes)
    process = multiprocessing.get_context("spawn").Process(
        target=_crash_child, args=(workspace, png_bytes, plan.id, boundary),
    )
    process.start()
    try:
        process.join(20)
        assert process.exitcode == 73
    finally:
        if process.is_alive():
            process.kill()
            process.join(5)
    with Container(workspace=workspace) as container:
        from imagen_cli.application.artifact_service import ArtifactService
        from imagen_cli.application.outcome_service import OutcomeService
        from imagen_cli.execution.foreground import ForegroundExecutor
        from tests.integration.test_approved_execution import OfflineAdapter

        adapter = OfflineAdapter(png_bytes)
        executor = ForegroundExecutor(
            container.repositories, adapter,
            ArtifactService(container.repositories, container.artifact_store),
            OutcomeService(container.repositories), container.private_state_store,
        )
        result = execute(service(container, validator, executor), plan)
        assert result["status"] in {"unknown", "save_failed"}
        assert adapter.requests == []
        assert len(container.repositories.list("jobs")) == 1
        assert container.repositories.get("approvals", approval.id)["state"] == "consumed"
        assert result["job"]["dispatch_state"] == (
            "response_received" if boundary == "response" else "intent_recorded"
        )
        assert result["job"]["remote_state"] == (
            "succeeded" if boundary == "response" else "not_submitted"
        )
        if boundary == "response":
            assert executor.read_private_response(result["job"]["id"]) == {}
        creates = Path(workspace) / "creates.txt"
        assert (len(creates.read_text().splitlines()) if creates.exists() else 0) == (
            0 if boundary == "intent" else 1
        )
