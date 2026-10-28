"""Oversized local transforms require a request-scoped decision before processing."""

import hashlib
import io

import pytest
from PIL import Image

from imagen_cli.application import postprocess_service, postprocess_worker
from imagen_cli.cli.commands.postprocess import transform_
from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.database import connect
from imagen_cli.storage.repositories import Repositories


def test_budget_excess_is_an_actionable_confirmation():
    with pytest.raises(ValueError) as caught:
        postprocess_service.check_budget((2048, 2048), [{"op": "superres"}])
    assert type(caught.value).__name__ == "BudgetApprovalRequired"
    assert caught.value.report["output_size"] == [8192, 8192]
    assert caught.value.report["output_pixels"] == 67_108_864


def test_confirmed_budget_does_not_weaken_dimension_validation():
    postprocess_service.check_budget((2048, 2048), [{"op": "superres"}], allow_large=True)
    with pytest.raises(ValueError, match="positive"):
        postprocess_service.check_budget((10, 10), [{"op": "resize", "params": {"width": -1}}],
                                        allow_large=True)


def test_cli_budget_consent_is_bound_to_source_and_pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(postprocess_service, "MAX_OUTPUT_PIXELS", 100)
    repositories = Repositories(connect(tmp_path / "state.sqlite3"))
    store = ArtifactStore(tmp_path / "artifacts", repositories)
    repositories.insert("sessions", {"id": "ses_a", "name": "a", "version": 0,
                                     "created_at": "2026-10-28T00:00:00Z"})
    source = "art_" + "a" * 32
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10), (12, 34, 56)).save(buffer, format="PNG")
    store.stage(source, buffer.getvalue())
    store.publish(source)
    original = hashlib.sha256(store.get(source).read_bytes()).hexdigest()
    try:
        status, data = transform_(repositories, store, source, op="resize:scale=2", session="ses_a")
        assert status == "approval_required"
        digest = data["budget"]["digest"]
        assert data["budget"]["output_size"] == [20, 20]
        assert len(repositories.list("artifacts")) == 1
        status, changed = transform_(repositories, store, source, op="resize:scale=3",
                                    session="ses_a", budget_approval=digest)
        assert status == "approval_required"
        assert changed["budget"]["digest"] != digest
        status, result = transform_(repositories, store, source, op="resize:scale=2",
                                   session="ses_a", budget_approval=digest)
        assert status == "succeeded"
        output = result["artifact"]
        assert (output["width"], output["height"]) == (20, 20)
        assert store.get(output["id"]).is_file()
        assert output["origin_manifest"]["budget_approval"] == digest
        assert hashlib.sha256(store.get(source).read_bytes()).hexdigest() == original
    finally:
        repositories.connection.close()


def test_cli_budget_roundtrip_uses_explicit_digest(tmp_path, monkeypatch):
    import json

    from imagen_cli.application.container import Container
    from imagen_cli.application.session_service import SessionService
    from imagen_cli.cli.entrypoint import main

    monkeypatch.setattr(postprocess_service, "MAX_OUTPUT_PIXELS", 100)
    root = tmp_path / "workspace"
    with Container(workspace=str(root)) as container:
        session = SessionService(
            container.repositories, container.artifact_store,
        ).create_session("a")
        buffer = io.BytesIO()
        Image.new("RGB", (10, 10)).save(buffer, format="PNG")
        identity = "art_" + "a" * 32
        container.artifact_store.stage(identity, buffer.getvalue())
        container.artifact_store.publish(identity)
        container.repositories.insert("artifact_links", {
            "session_id": session.id, "artifact_id": identity, "role": "output",
            "created_at": "2026-10-28T00:00:00Z",
        })
    arguments = ["--workspace", str(root), "transform", identity,
                 "--op", "resize:scale=2", "--agent", "--no-interactive"]
    output = io.StringIO()
    assert main(arguments, stdout=output, stderr=io.StringIO()) == 6
    value = json.loads(output.getvalue())
    assert value["status"] == "approval_required"
    assert "--budget-approval" in value["error"]["message"]
    output = io.StringIO()
    assert main([*arguments, "--budget-approval", value["data"]["budget"]["digest"]],
                stdout=output, stderr=io.StringIO()) == 0
    assert json.loads(output.getvalue())["data"]["artifact"]["width"] == 20


@pytest.mark.parametrize("answer,code,count", [("y\n", 0, 2), ("n\n", 6, 1)])
def test_cli_interactive_budget_decision(tmp_path, monkeypatch, answer, code, count):
    from imagen_cli.application.container import Container
    from imagen_cli.application.session_service import SessionService
    from imagen_cli.cli.entrypoint import main

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(postprocess_service, "MAX_OUTPUT_PIXELS", 100)
    monkeypatch.setattr("sys.stdin", Terminal(answer))
    root = tmp_path / "workspace"
    with Container(workspace=str(root)) as container:
        session = SessionService(
            container.repositories, container.artifact_store,
        ).create_session("a")
        buffer = io.BytesIO()
        Image.new("RGB", (10, 10)).save(buffer, format="PNG")
        identity = "art_" + "a" * 32
        container.artifact_store.stage(identity, buffer.getvalue())
        container.artifact_store.publish(identity)
        container.repositories.insert("artifact_links", {
            "session_id": session.id, "artifact_id": identity, "role": "output",
            "created_at": "2026-10-28T00:00:00Z",
        })
    error = io.StringIO()
    assert main(["--workspace", str(root), "transform", identity, "--op", "resize:scale=2"],
                stdout=Terminal(), stderr=error) == code
    assert "[y/N]" in error.getvalue()
    with Container(workspace=str(root)) as container:
        assert len(container.repositories.list("artifacts")) == count


def test_budget_digest_changes_when_source_content_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(postprocess_service, "MAX_OUTPUT_PIXELS", 100)
    path = tmp_path / "source.png"
    pipeline = [{"op": "resize", "params": {"scale": 2}}]
    Image.new("RGB", (10, 10), "red").save(path)
    with pytest.raises(postprocess_service.BudgetApprovalRequired) as first:
        postprocess_service.budget_preflight(path, pipeline)
    digest = first.value.report["digest"]
    Image.new("RGB", (10, 10), "blue").save(path)
    with pytest.raises(postprocess_service.BudgetApprovalRequired) as changed:
        postprocess_service.budget_preflight(path, pipeline, digest)
    assert changed.value.report["digest"] != digest


def test_confirmed_byte_budget_survives_reopen(tmp_path, monkeypatch):
    from imagen_cli.storage import artifacts

    repositories = Repositories(connect(tmp_path / "state.sqlite3"))
    store = ArtifactStore(tmp_path / "artifacts", repositories)
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buffer, format="PNG")
    data = buffer.getvalue()
    monkeypatch.setattr(artifacts, "MAX_BYTES", len(data) - 1)
    identity = "art_" + "b" * 32
    manifest = {"kind": "postprocess", "budget_approval": "b" * 64,
                "budget": {"output_pixels": 100}}
    try:
        with pytest.raises(ValueError, match="byte"):
            store.stage(identity, data)
        store.stage(identity, data, origin_manifest=manifest, allow_large=True)
        store.publish(identity)
        assert store.get(identity).read_bytes() == data
    finally:
        repositories.connection.close()


def test_worker_runs_only_after_current_budget_consent(tmp_path, monkeypatch):
    monkeypatch.setattr(postprocess_service, "MAX_OUTPUT_PIXELS", 100)
    source = tmp_path / "source.png"
    Image.new("RGB", (10, 10)).save(source)
    with pytest.raises(ValueError) as caught:
        postprocess_worker.execute(source, [{"op": "resize", "params": {"scale": 2}}])
    assert type(caught.value).__name__ == "BudgetApprovalRequired"
    digest = caught.value.report["digest"]
    data, meta = postprocess_worker.execute(source, [{"op": "resize", "params": {"scale": 2}}],
                                           budget_approval=digest)
    assert meta["budget_approval"] == digest
    with Image.open(io.BytesIO(data)) as output:
        assert output.size == (20, 20)


def test_confirmed_local_output_can_be_saved_and_reopened(tmp_path, monkeypatch):
    from imagen_cli.storage import artifacts

    monkeypatch.setattr(artifacts, "MAX_PIXELS", 100)
    repositories = Repositories(connect(tmp_path / "state.sqlite3"))
    store = ArtifactStore(tmp_path / "artifacts", repositories)
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20)).save(buffer, format="PNG")
    data = buffer.getvalue()
    try:
        with pytest.raises(ValueError, match="pixel"):
            store.stage("art_" + "1" * 32, data)
        manifest = {"kind": "postprocess", "budget_approval": "a" * 64,
                    "budget": {"output_size": [20, 20], "output_pixels": 400}}
        identity = "art_" + "2" * 32
        with pytest.raises(ValueError, match="pixel"):
            store.stage(identity, data, origin_manifest=manifest)
        store.stage(identity, data, origin_manifest=manifest, allow_large=True)
        store.publish(identity)
        assert store.get(identity).read_bytes() == data
    finally:
        repositories.connection.close()
