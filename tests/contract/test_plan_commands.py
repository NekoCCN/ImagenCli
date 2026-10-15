"""Persisted plans can be inspected and approved without dispatch."""

from imagen_cli.application.container import Container
from tests.contract.test_creation_planning_cli import seed
from tests.integration.test_local_cli import invoke


def test_plan_review_and_approval_are_separate_from_execution(tmp_path):
    workspace = tmp_path / "work"
    prompt = seed(workspace, tmp_path)
    code, result = invoke(workspace, "generate", "--session", "poster", "--prompt-file",
                          str(prompt), "--dry-run")
    assert code == 0
    plan = result["data"]["plan"]
    code, shown = invoke(workspace, "plan", "show", plan["id"])
    assert code == 0
    assert shown["data"]["plan"]["plan_digest"] == plan["plan_digest"]
    assert invoke(workspace, "plan", "approve", plan["id"], "--digest", "0" * 64)[0] == 2
    code, approved = invoke(workspace, "plan", "approve", plan["id"],
                            "--digest", plan["plan_digest"])
    assert code == 0
    assert approved["status"] == "approved"
    assert approved["data"]["approval"]["state"] == "approved"
    with Container(workspace=str(workspace)) as container:
        assert container.repositories.list("jobs") == []
        assert container.repositories.list("turns") == []


def test_missing_plan_does_not_claim_success(tmp_path):
    assert invoke(tmp_path / "work", "plan", "show", "pln_" + "1" * 32)[0] == 4
