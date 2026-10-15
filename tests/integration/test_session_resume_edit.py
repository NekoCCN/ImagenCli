"""Offline session persistence journeys; each child owns its SQLite connection."""

import hashlib
import multiprocessing
import socket

import pytest

from imagen_cli.application.container import Container


def _service(container):
    from imagen_cli.application.session_service import SessionService

    return SessionService(container.repositories, container.artifact_store)


def _turn(service, session_id, **changes):
    values = {
        "instruction": "a red square",
        "operation": "generate",
        "resolved_profile_snapshot": {"id": "offline", "revision": 1},
        "model": "offline-image",
        "parameters": {},
        "requested_count": 2,
    }
    values.update(changes)
    return service.create_turn(session_id, **values)


def _complete(container, turn, count=2):
    container.repositories.update(
        "jobs",
        turn.job_id,
        {
            "state": "succeeded",
            "remote_state": "succeeded",
            "local_save_state": "saved",
            "actual_count": count,
            "dispatch_state": "response_received",
        },
    )
    container.repositories.update("turns", turn.id, {"creation_state": "succeeded"})


def _generate_child(workspace, data, pipe):
    # A hard exit after durable publication must not need Container.close to commit.
    import os

    socket.socket = lambda *a, **k: (_ for _ in ()).throw(AssertionError("network forbidden"))
    with Container(workspace=str(workspace)) as container:
        service = _service(container)
        session = service.create_session("journey", intent="offline revision history")
        turn = _turn(service, session.id)
        artifacts = [service.save_artifact(turn.id, data) for _ in range(2)]
        _complete(container, turn)
        assert service.publish_turn(turn.id)
        pipe.send((session.id, turn.id, [a.id for a in artifacts]))
        pipe.close()
        os._exit(0)


def _edit_child(workspace, session_id, parent_id, selected_id, data, pipe):
    socket.socket = lambda *a, **k: (_ for _ in ()).throw(AssertionError("network forbidden"))
    with Container(workspace=str(workspace)) as container:
        service = _service(container)
        restored = service.resume(session_id)
        assert restored["session"].current_turn_id == parent_id
        turn = _turn(
            service,
            session_id,
            operation="edit",
            instruction="make the square blue",
            requested_count=1,
            parent_turn_id=parent_id,
            references={"input": [selected_id]},
        )
        artifact = service.save_artifact(turn.id, data)
        _complete(container, turn, 1)
        assert service.publish_turn(turn.id)
        pipe.send((turn.id, artifact.id))


def _run_child(target, *args):
    ctx = multiprocessing.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    process = ctx.Process(target=target, args=(*args, sender))
    process.start()
    sender.close()
    try:
        assert receiver.poll(20), "child did not persist its result"
        result = receiver.recv()
        process.join(20)
        assert process.exitcode == 0
        return result
    finally:
        if process.is_alive():
            process.kill()
            process.join(5)
        receiver.close()


def test_generate_hard_exit_restore_and_edit_selected_candidate(tmp_path, png_bytes):
    workspace = tmp_path / "workspace"
    session_id, parent_id, originals = _run_child(_generate_child, workspace, png_bytes)
    edit_id, output_id = _run_child(
        _edit_child,
        workspace,
        session_id,
        parent_id,
        originals[1],
        png_bytes,
    )
    with Container(workspace=str(workspace)) as container:
        restored = _service(container).resume(session_id)
        assert restored["session"].version == 2
        assert restored["session"].current_turn_id == edit_id
        assert [turn.id for turn in restored["turns"]] == [parent_id, edit_id]
        assert restored["turns"][1].parent_turn_id == parent_id
        refs = restored["references"]
        assert [(r.turn_id, r.role, r.index, r.artifact_id) for r in refs] == [
            (parent_id, "output", 0, originals[0]),
            (parent_id, "output", 1, originals[1]),
            (edit_id, "input", 0, originals[1]),
            (edit_id, "output", 0, output_id),
        ]
        assert len(set(originals + [output_id])) == 3
        for identity in originals + [output_id]:
            artifact = container.repositories.get("artifacts", identity)
            assert artifact["sha256"] == hashlib.sha256(png_bytes).hexdigest()
            assert (artifact["media_type"], artifact["width"], artifact["height"]) == (
                "image/png",
                2,
                3,
            )
            assert container.artifact_store.get(identity).read_bytes() == png_bytes
        assert container.repositories.get("artifacts", output_id)["producer_turn_id"] == edit_id
        assert container.connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_ordered_references_and_invalid_attempt_roll_back(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        service = _service(container)
        session = service.create_session("one")
        other = service.create_session("two")
        source = _turn(service, session.id)
        images = [service.save_artifact(source.id, png_bytes) for _ in range(2)]
        foreign = _turn(service, other.id)
        for changes in [
            {"parent_turn_id": foreign.id},
            {"references": {"input": [images[0].id, "art_missing"]}},
            {"references": {"output": [images[0].id]}},
        ]:
            with pytest.raises((ValueError, KeyError)):
                _turn(service, session.id, **changes)
        assert len(container.repositories.list("turns")) == 2
        assert len(container.repositories.list("jobs")) == 2
        edited = _turn(
            service,
            session.id,
            operation="edit",
            parent_turn_id=source.id,
            references={"input": [images[1].id, images[0].id], "reference": [images[0].id]},
        )
        assert edited.input_manifest == [
            {"artifact_id": images[1].id, "role": "input", "index": 0},
            {"artifact_id": images[0].id, "role": "input", "index": 1},
            {"artifact_id": images[0].id, "role": "reference", "index": 0},
        ]
        restored = service.resume(session.id)
        assert [r.artifact_id for r in restored["references"] if r.turn_id == edited.id] == [
            images[1].id,
            images[0].id,
            images[0].id,
        ]


def test_stale_cas_preserves_both_successful_jobs_and_files(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as first, Container(workspace=str(tmp_path)) as second:
        service = _service(first)
        competing = _service(second)
        session = service.create_session("race")
        turns = [_turn(service, session.id), _turn(competing, session.id)]
        outputs = []
        for owner, api, turn in zip([first, second], [service, competing], turns, strict=True):
            outputs.extend(api.save_artifact(turn.id, png_bytes) for _ in range(2))
            _complete(owner, turn)
        assert service.publish_turn(turns[0].id)
        assert not competing.publish_turn(turns[1].id)
        # Re-observation must not turn an already published turn into a conflict.
        assert service.publish_turn(turns[0].id)
        current = service.resume(session.id)
        assert current["session"].current_turn_id == turns[0].id
        assert current["session"].version == 1
        assert [t.publication_state for t in current["turns"]] == ["published", "conflict"]
        for turn in turns:
            assert first.repositories.get("jobs", turn.job_id)["state"] == "succeeded"
        for artifact in outputs:
            assert first.artifact_store.get(artifact.id).read_bytes() == png_bytes


@pytest.mark.parametrize("state", ["queued", "running", "failed", "partial_success", "unknown"])
def test_incomplete_turn_cannot_replace_current(tmp_path, png_bytes, state):
    with Container(workspace=str(tmp_path)) as container:
        service = _service(container)
        session = service.create_session("incomplete")
        turn = _turn(service, session.id)
        service.save_artifact(turn.id, png_bytes)
        container.repositories.update("turns", turn.id, {"creation_state": state})
        with pytest.raises(ValueError):
            service.publish_turn(turn.id)
        assert service.resume(session.id)["session"].current_turn_id is None
        assert container.repositories.get("turns", turn.id)["publication_state"] == "not_attempted"


def test_success_requires_saved_outputs_and_complete_job(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        service = _service(container)
        session = service.create_session("missing")
        turn = _turn(service, session.id)
        _complete(container, turn)
        with pytest.raises(ValueError):
            service.publish_turn(turn.id)
        service.save_artifact(turn.id, png_bytes)
        with pytest.raises(ValueError):
            service.publish_turn(turn.id)
        service.save_artifact(turn.id, png_bytes)
        container.repositories.update("jobs", turn.job_id, {"local_save_state": "partial"})
        with pytest.raises(ValueError):
            service.publish_turn(turn.id)
        container.repositories.update("jobs", turn.job_id, {"local_save_state": "saved"})
        assert service.publish_turn(turn.id)


def test_staging_input_and_deleted_session_are_rejected(tmp_path, png_bytes):
    from imagen_cli.domain.primitive import new_id

    with Container(workspace=str(tmp_path)) as container:
        service = _service(container)
        session = service.create_session("deleted")
        source = _turn(service, session.id)
        staged = container.artifact_store.stage(
            new_id("art"),
            png_bytes,
            producer_turn_id=source.id,
            producer_job_id=source.job_id,
        )
        with pytest.raises(ValueError):
            _turn(service, session.id, references={"input": [staged["id"]]})
        container.repositories.update(
            "sessions", session.id, {"deleted_at": "2026-10-23T00:00:00Z"}
        )
        with pytest.raises(ValueError):
            service.resume(session.id)
        with pytest.raises(ValueError):
            _turn(service, session.id)
        assert len(container.repositories.list("turns")) == 1
