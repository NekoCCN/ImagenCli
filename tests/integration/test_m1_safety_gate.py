"""M1 safety gate: nothing paid is sent before approval, twice, or after drift.

T108's ten-process concurrency acceptance stays in
`tests/integration/test_session_concurrency.py`; this file aggregates the
remaining M1 safety properties in one place.
"""

import pytest

from imagen_cli.application.container import Container
from imagen_cli.application.planning_service import PlanningService
from tests.integration.test_approved_execution import execute, service, setup


def _api(container, validator, executor):
    return service(container, validator, executor)


def test_planning_and_approval_stay_offline(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        plan, _, validator, executor, adapter, _ = setup(
            container, png_bytes, approved=False,
        )
        assert adapter.requests == []
        PlanningService(container.repositories).approve(plan.id, plan.plan_digest)
        assert adapter.requests == []
        assert container.repositories.list("jobs") == []
        assert container.repositories.list("artifacts") == []


def test_unapproved_plan_never_dispatches(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        plan, _, validator, executor, adapter, _ = setup(
            container, png_bytes, approved=False,
        )
        with pytest.raises(ValueError):
            execute(_api(container, validator, executor), plan)
        assert adapter.requests == []
        assert container.repositories.list("jobs") == []
        assert container.repositories.list("turns") == []


def test_profile_change_after_approval_is_rejected_before_dispatch(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        plan, approval, validator, executor, adapter, _ = setup(container, png_bytes)
        container.repositories.update(
            "profiles", "offline",
            {"base_url": "https://other.example.test/v1", "revision": 2},
        )
        with pytest.raises(ValueError):
            execute(_api(container, validator, executor), plan)
        assert adapter.requests == []
        assert container.repositories.list("jobs") == []
        assert container.repositories.get("approvals", approval.id)["state"] == "approved"


def test_save_failure_and_replay_never_create_twice(tmp_path, png_bytes, monkeypatch):
    with Container(workspace=str(tmp_path)) as container:
        plan, _, validator, executor, adapter, _ = setup(container, png_bytes)

        def disk_full(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(executor.artifact_service, "save", disk_full)
        api = _api(container, validator, executor)
        first = execute(api, plan)
        assert first["status"] == "save_failed"
        assert len(adapter.requests) == 1
        assert execute(api, plan) == first
        assert len(adapter.requests) == 1
        assert len(container.repositories.list("jobs")) == 1


def test_async_generation_is_refused_without_side_effects(tmp_path, capsys):
    with Container(workspace=str(tmp_path)) as container:
        assert container.execute("generate", [])[0] == "unsupported_capability"
        assert container.repositories.list("jobs") == []

    from imagen_cli.cli.entrypoint import main

    code = main([
        "--workspace", str(tmp_path), "--output", "json",
        "generate", "--async", "--prompt", "red kite", "--session", "ses_" + "0" * 32,
    ])
    assert code != 0
    # `--async` is an unavailable option, so parsing refuses it before any work.
    assert '"ok":false' in capsys.readouterr().out
    with Container(workspace=str(tmp_path)) as container:
        assert container.repositories.list("jobs") == []


def test_output_failure_does_not_rerun_the_command(tmp_path, png_bytes, monkeypatch):
    with Container(workspace=str(tmp_path)) as container:
        plan, _, validator, executor, adapter, _ = setup(container, png_bytes)
        result = execute(_api(container, validator, executor), plan)
        assert result["status"] == "succeeded"
        job_id = result["job"]["id"]
        assert len(adapter.requests) == 1

    from imagen_cli.cli import renderers
    from imagen_cli.cli.entrypoint import main

    def broken_pipe(self, value):
        raise BrokenPipeError

    monkeypatch.setattr(renderers.Renderer, "finish", broken_pipe)
    code = main([
        "--workspace", str(tmp_path), "--output", "json", "job", "get", job_id,
    ])
    assert code == 1
    with Container(workspace=str(tmp_path)) as container:
        assert len(container.repositories.list("jobs")) == 1
        assert len(container.repositories.list("artifacts")) == 1


def test_preview_failure_does_not_recreate_the_job(tmp_path, png_bytes):
    from imagen_cli.cli.preview import preview

    with Container(workspace=str(tmp_path)) as container:
        plan, _, validator, executor, adapter, _ = setup(container, png_bytes)
        result = execute(_api(container, validator, executor), plan)
        assert result["status"] == "succeeded"
        path = container.artifact_store.get(result["artifacts"][0]["id"])

        class Broken:
            def isatty(self):
                return True

            def write(self, value):
                raise OSError("terminal gone")

            def flush(self):
                pass

        import os

        os.environ["TERM"] = "xterm-256color"
        try:
            assert preview(path, Broken()) is False
        finally:
            os.environ.pop("TERM", None)
        assert len(adapter.requests) == 1
        assert len(container.repositories.list("jobs")) == 1
