"""Six-step batch summary priority, including partial files combined with unknown."""

from imagen_cli.application.batch_summary import summarize


def child(**changes):
    value = {
        "job_id": "job_x",
        "state": "succeeded",
        "remote_state": "succeeded",
        "local_save_state": "saved",
        "actual_count": 1,
        "saved_artifact_count": 1,
    }
    value.update(changes)
    return value


def test_all_complete_children_succeed_with_independent_counts():
    summary = summarize([
        child(job_id="job_1", actual_count=2, saved_artifact_count=2),
        child(job_id="job_2", actual_count=1, saved_artifact_count=3),
    ])
    assert summary["state"] == "succeeded"
    assert summary["complete_success_count"] == 2
    assert summary["saved_artifact_count"] == 5


def test_active_child_keeps_batch_running_before_other_priorities():
    summary = summarize([
        child(job_id="job_1"),
        child(job_id="job_2", state="failed", remote_state="failed",
              local_save_state="not_started", actual_count=0, saved_artifact_count=0),
        child(job_id="job_3", state="running", remote_state="running",
              local_save_state="not_started", actual_count=0, saved_artifact_count=0),
        child(job_id="job_4", state="unknown", remote_state="unknown",
              local_save_state="not_started", actual_count=1, saved_artifact_count=0),
    ])
    assert summary["state"] == "running"
    assert summary["complete_success_count"] == 1


def test_complete_success_plus_failed_or_unknown_is_partial_success():
    summary = summarize([
        child(job_id="job_1", actual_count=2, saved_artifact_count=2),
        child(job_id="job_2", state="failed", remote_state="failed",
              local_save_state="not_started", actual_count=0, saved_artifact_count=0),
        child(job_id="job_3", state="unknown", remote_state="unknown",
              local_save_state="not_started", actual_count=1, saved_artifact_count=0),
    ])
    assert summary["state"] == "partial_success"
    assert summary["complete_success_count"] == 1
    assert summary["saved_artifact_count"] == 2


def test_partial_files_plus_unknown_preserves_both_evidence_kinds():
    summary = summarize([
        child(job_id="job_1", state="failed", remote_state="succeeded",
              local_save_state="partial", actual_count=3, saved_artifact_count=2),
        child(job_id="job_2", state="unknown", remote_state="unknown",
              local_save_state="not_started", actual_count=1, saved_artifact_count=0),
    ])
    assert summary["state"] == "unknown"
    assert summary["complete_success_count"] == 0
    assert summary["saved_artifact_count"] == 2


def test_partial_files_without_unknown_are_partial_success():
    summary = summarize([
        child(job_id="job_1", state="failed", remote_state="succeeded",
              local_save_state="partial", actual_count=2, saved_artifact_count=1),
        child(job_id="job_2", state="failed", remote_state="failed",
              local_save_state="not_started", actual_count=0, saved_artifact_count=0),
    ])
    assert summary["state"] == "partial_success"
    assert summary["complete_success_count"] == 0
    assert summary["saved_artifact_count"] == 1


def test_unknown_without_success_or_partial_files_is_unknown():
    summary = summarize([
        child(job_id="job_1", state="unknown", remote_state="unknown",
              local_save_state="not_started", actual_count=1, saved_artifact_count=0),
        child(job_id="job_2", state="failed", remote_state="failed",
              local_save_state="not_started", actual_count=0, saved_artifact_count=0),
    ])
    assert summary["state"] == "unknown"
    assert summary["complete_success_count"] == 0


def test_two_saved_files_never_replace_two_complete_jobs():
    summary = summarize([
        child(job_id="job_1", actual_count=2, saved_artifact_count=2),
        child(job_id="job_2", state="unknown", remote_state="unknown",
              local_save_state="not_started", actual_count=1, saved_artifact_count=0),
    ])
    assert summary["complete_success_count"] == 1
    assert summary["saved_artifact_count"] == 2
    assert summary["state"] == "partial_success"


def test_failed_only_is_failed_and_confirmed_cancel_only_is_canceled():
    failed = summarize([
        child(job_id="job_1", state="failed", remote_state="failed",
              local_save_state="not_started", actual_count=0, saved_artifact_count=0),
    ])
    canceled = summarize([
        child(job_id="job_1", state="canceled", remote_state="canceled",
              local_save_state="not_started", actual_count=0, saved_artifact_count=0),
    ])
    mixed = summarize([
        child(job_id="job_1", state="canceled", remote_state="canceled",
              local_save_state="not_started", actual_count=0, saved_artifact_count=0),
        child(job_id="job_2", state="failed", remote_state="failed",
              local_save_state="not_started", actual_count=0, saved_artifact_count=0),
    ])
    assert failed["state"] == "failed"
    assert canceled["state"] == "canceled"
    assert mixed["state"] == "failed"


def test_success_state_without_saved_files_is_not_a_complete_job():
    summary = summarize([
        child(job_id="job_1", state="succeeded", remote_state="succeeded",
              local_save_state="failed", actual_count=1, saved_artifact_count=0),
    ])
    assert summary["state"] == "failed"
    assert summary["complete_success_count"] == 0
    assert summary["saved_artifact_count"] == 0
