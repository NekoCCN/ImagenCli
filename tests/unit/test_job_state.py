"""Axis transitions are validated and every change is a durable event."""

import pytest

from imagen_cli.application.container import Container
from imagen_cli.application.job_state import JobState
from tests.integration.test_approved_execution import setup


def test_transitions_validate_axes_and_append_ordered_events(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        from tests.integration.test_approved_execution import execute, service

        plan, _, validator, executor, _, _ = setup(container, png_bytes)
        execute(service(container, validator, executor), plan)
        job_id = container.repositories.list("jobs")[0]["id"]
        state = JobState(container.repositories)
        with pytest.raises(ValueError):
            state.record(job_id, event="bad", colour="red")
        with pytest.raises(ValueError):
            state.record(job_id, event="bad", state="exploded")
        with pytest.raises(ValueError):
            state.record(job_id, event="bad", dispatch_state="not_started")
        with pytest.raises(ValueError):
            state.record(job_id, event="bad", state="running")
        events = container.repositories.list("events", job_id=job_id)
        sequences = [row["job_seq"] for row in events]
        assert sequences == sorted(sequences)
        assert [row["event_type"] for row in events] == [
            "dispatch_intent_recorded", "response_received",
        ]


def test_unknown_axis_never_rewrites_terminal_facts(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        plan, _, validator, executor, _, _ = setup(container, png_bytes)
        from tests.integration.test_approved_execution import execute, service

        execute(service(container, validator, executor), plan)
        job = container.repositories.list("jobs")[0]
        assert job["state"] == "succeeded"
        with pytest.raises(ValueError):
            JobState(container.repositories).record(job["id"], event="late", state="failed")
        assert container.repositories.get("jobs", job["id"])["state"] == "succeeded"
