"""Ten approved executions preserve every result while competing for one version."""

import multiprocessing
import socket

from imagen_cli.application.container import Container
from imagen_cli.application.planning_service import PlanningService
from imagen_cli.domain.models import ExecutionPlan
from tests.integration.test_approved_execution import execute, service, setup


def _execute_child(workspace, plan_id, png_bytes, barrier):
    from imagen_cli.application.artifact_service import ArtifactService
    from imagen_cli.application.outcome_service import OutcomeService
    from imagen_cli.capabilities.catalog import CapabilityCatalog
    from imagen_cli.capabilities.validator import CapabilityValidator
    from imagen_cli.execution.foreground import ForegroundExecutor
    from tests.integration.test_approved_execution import OfflineAdapter
    from tests.unit.test_capabilities import capability

    socket.socket = lambda *a, **k: (_ for _ in ()).throw(AssertionError("network forbidden"))
    with Container(workspace=workspace) as container:
        plan = ExecutionPlan.model_validate(container.repositories.get("plans", plan_id))
        record = capability(
            parameter_schema={"type": "object", "properties": {"count": {"const": 1}},
                              "additionalProperties": False},
            continuation_strategy="none",
        )
        validator = CapabilityValidator(CapabilityCatalog([record]))
        adapter = OfflineAdapter(png_bytes, observe=lambda request: barrier.wait(timeout=30))
        executor = ForegroundExecutor(
            container.repositories, adapter,
            ArtifactService(container.repositories, container.artifact_store),
            OutcomeService(container.repositories), container.private_state_store,
        )
        api = service(container, validator, executor)
        result = execute(api, plan)
        assert result["status"] in {"succeeded", "conflict"}
        assert execute(api, plan) == result
        assert len(adapter.requests) == 1


def test_ten_processes_keep_outputs_and_publish_exactly_one_version(tmp_path, png_bytes):
    workspace = str(tmp_path / "workspace")
    with Container(workspace=workspace) as container:
        parent_plan, _, validator, executor, _, _ = setup(container, png_bytes)
        parent = execute(service(container, validator, executor), parent_plan)
        assert parent["status"] == "succeeded"
        planning = PlanningService(container.repositories)
        plans = []
        for index in range(10):
            plan, _ = planning.create_plan(
                session_id=parent_plan.session_id,
                target_snapshot=parent_plan.target_snapshot,
                operation="generate", model=parent_plan.model,
                normalized_parameters={"count": 1}, prompt_snapshot=f"candidate {index}",
                input_hashes=[], portable_context={"kind": "none"},
                capability_digest=parent_plan.capability_digest,
            )
            planning.approve(plan.id, plan.plan_digest)
            plans.append(plan)
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(10)
    processes = [ctx.Process(target=_execute_child,
                             args=(workspace, plan.id, png_bytes, barrier)) for plan in plans]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(45)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.pid is not None and process.is_alive():
                process.kill()
                process.join(5)
    with Container(workspace=workspace) as container:
        turns = [row for row in container.repositories.list("turns")
                 if row["id"] != parent["turn"]["id"]]
        assert len(turns) == 10
        assert {row["parent_turn_id"] for row in turns} == {parent["turn"]["id"]}
        assert {row["expected_session_version"] for row in turns} == {1}
        assert sum(row["publication_state"] == "published" for row in turns) == 1
        assert sum(row["publication_state"] == "conflict" for row in turns) == 9
        assert all(row["creation_state"] == "succeeded" for row in turns)
        assert len(container.repositories.list("jobs")) == 11
        artifacts = container.repositories.list("artifacts")
        assert len(artifacts) == 11
        for artifact in artifacts:
            assert container.artifact_store.get(artifact["id"]).read_bytes() == png_bytes
        session = container.repositories.get("sessions", parent_plan.session_id)
        assert session["version"] == 2
        assert session["current_turn_id"] in {
            row["id"] for row in turns if row["publication_state"] == "published"
        }
        assert container.connection.execute("PRAGMA foreign_key_check").fetchall() == []
