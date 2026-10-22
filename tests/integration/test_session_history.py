"""Fork/export/import/delete keep history traceable and GC reference-safe."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from imagen_cli.application import postprocess_service
from imagen_cli.application.cleanup_service import collect, delete_session
from imagen_cli.application.container import Container
from imagen_cli.application.export_service import export_session
from imagen_cli.application.fork_service import fork
from imagen_cli.application.import_service import import_archive
from imagen_cli.application.session_service import SessionService
from imagen_cli.domain.primitive import new_id
from tests.integration.test_approved_execution import execute, service, setup
from tests.integration.test_local_cli import invoke


def _executed(container, png_bytes):
    plan, _, validator, executor, _, _ = setup(container, png_bytes)
    result = execute(service(container, validator, executor), plan)
    return plan, result


def test_fork_records_origin_and_keeps_source_unchanged(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        plan, result = _executed(container, png_bytes)
        source_before = container.repositories.get("sessions", plan.session_id)
        forked = fork(
            container.repositories, plan.session_id, turn_id=result["turn"]["id"],
            artifact_ids=(result["artifacts"][0]["id"],), name="分支一",
        )
        assert forked["origin_session_id"] == plan.session_id
        assert forked["origin_turn_id"] == result["turn"]["id"]
        assert forked["version"] == 0 and forked["current_turn_id"] is None
        assert container.repositories.get("sessions", plan.session_id) == source_before


def test_fork_rejects_foreign_turn_and_unrelated_artifact(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        plan, result = _executed(container, png_bytes)
        with pytest.raises(ValueError):
            fork(container.repositories, plan.session_id, turn_id="trn_" + "0" * 32)
        with pytest.raises(ValueError):
            fork(container.repositories, plan.session_id,
                 turn_id=result["turn"]["id"], artifact_ids=("art_" + "0" * 32,))


def test_delete_is_logical_and_gc_is_confirmed(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        plan, result = _executed(container, png_bytes)
        artifact_id = result["artifacts"][0]["id"]
        path = container.artifact_store.get(artifact_id)
        assert path.exists()

        dry = collect(container.repositories, container.artifact_store)
        assert artifact_id not in {item["id"] for item in dry["candidates"]}

        delete_session(container.repositories, plan.session_id)
        session = container.repositories.get("sessions", plan.session_id)
        assert session["deleted_at"] is not None
        dry = collect(container.repositories, container.artifact_store)
        assert artifact_id in {item["id"] for item in dry["candidates"]}
        assert path.exists()

        applied = collect(
            container.repositories, container.artifact_store, confirmed=(artifact_id,),
        )
        assert applied["deleted"] == [artifact_id]
        assert not path.exists()


def test_gc_keeps_derived_artifacts_owned_by_live_sessions(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        plan, result = _executed(container, png_bytes)
        source = result["artifacts"][0]["id"]
        derived = postprocess_service.apply(
            container.repositories, container.artifact_store, source,
            [{"op": "pixelate", "params": {"scale": 2}}],
        )["id"]
        candidates = {
            item["id"] for item in
            collect(container.repositories, container.artifact_store)["candidates"]
        }
        assert source not in candidates and derived not in candidates


def test_gc_removes_derived_artifacts_with_session_links(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        plan, result = _executed(container, png_bytes)
        source = result["artifacts"][0]["id"]
        derived = postprocess_service.apply(
            container.repositories, container.artifact_store, source,
            [{"op": "pixelate", "params": {"scale": 2}}],
        )["id"]
        delete_session(container.repositories, plan.session_id)
        dry = collect(container.repositories, container.artifact_store)
        assert {source, derived} <= {item["id"] for item in dry["candidates"]}
        applied = collect(
            container.repositories, container.artifact_store,
            confirmed=(source, derived),
        )
        assert set(applied["deleted"]) == {source, derived}


def test_delete_releases_the_name_for_reuse(tmp_path):
    with Container(workspace=str(tmp_path)) as container:
        service_ = SessionService(container.repositories, container.artifact_store)
        first = service_.create_session("同名会话")
        delete_session(container.repositories, first.id)
        second = service_.create_session("同名会话")
        assert second.id != first.id
        assert second.name == "同名会话"
        row = container.repositories.get("sessions", first.id)
        assert row["deleted_at"] is not None and row["name"] != "同名会话"


def _seed(container, png_bytes, name="seed"):
    service_ = SessionService(container.repositories, container.artifact_store)
    session = service_.create_session(name)
    turn = service_.create_turn(
        session.id, instruction="draw", operation="generate",
        resolved_profile_snapshot={}, model="local", parameters={},
    )
    artifact = service_.save_artifact(turn.id, png_bytes)
    return session.id, turn.id, artifact.id


def test_concurrent_forks_and_exports_keep_the_source_unchanged(tmp_path, png_bytes):
    workspace = str(tmp_path / "work")
    with Container(workspace=workspace) as container:
        session_id, turn_id, artifact_id = _seed(container, png_bytes)
        before = container.repositories.get("sessions", session_id)

    def worker(index):
        with Container(workspace=workspace) as container:
            row = fork(
                container.repositories, session_id, turn_id=turn_id,
                artifact_ids=(artifact_id,), name=f"fork-{index}",
            )
            return row["id"]

    with ThreadPoolExecutor(max_workers=6) as pool:
        identities = list(pool.map(worker, range(6)))
    assert len(set(identities)) == 6
    with Container(workspace=workspace) as container:
        assert container.repositories.get("sessions", session_id) == before
        for identity in identities:
            row = container.repositories.get("sessions", identity)
            assert row["origin_session_id"] == session_id
            assert row["origin_turn_id"] == turn_id
        destinations = [tmp_path / f"export-{index}.zip" for index in range(4)]

        def exporter(index):
            with Container(workspace=workspace) as other:
                export_session(
                    other.repositories, other.artifact_store,
                    session_id, destinations[index],
                )

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(exporter, range(4)))
        for destination in destinations:
            assert destination.is_file()


def test_concurrent_imports_remap_into_distinct_sessions(tmp_path, png_bytes):
    source_workspace = str(tmp_path / "source")
    target_workspace = str(tmp_path / "target")
    archive = tmp_path / "history.zip"
    with Container(workspace=source_workspace) as source:
        session_id, _, artifact_id = _seed(source, png_bytes)
        export_session(source.repositories, source.artifact_store, session_id, archive)
    # Initialize the shared schema before racing writers (migration is not designed to race).
    with Container(workspace=target_workspace) as target:
        assert target.repositories.list("sessions") == []

    barrier = threading.Barrier(4)

    def importer(_):
        with Container(workspace=target_workspace) as target:
            barrier.wait(timeout=20)
            return import_archive(target.repositories, target.artifact_store, archive)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(importer, range(4)))
    sessions = {result["session"]["id"] for result in results}
    artifacts = {result["origin_map"]["artifacts"][artifact_id] for result in results}
    names = {result["session"]["name"] for result in results}
    assert len(sessions) == 4 and len(artifacts) == 4 and len(names) == 4
    with Container(workspace=target_workspace) as target:
        assert len(target.repositories.list("sessions")) == 4
        for identity in artifacts:
            assert target.artifact_store.get(identity).read_bytes() == png_bytes


def test_delete_keeps_exports_and_gc_protects_shared_references(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path / "work")) as container:
        service_ = SessionService(container.repositories, container.artifact_store)
        first = service_.create_session("first")
        turn = service_.create_turn(
            first.id, instruction="draw", operation="generate",
            resolved_profile_snapshot={}, model="local", parameters={},
        )
        artifact = service_.save_artifact(turn.id, png_bytes)
        second = service_.create_session("second")
        service_.create_turn(
            second.id, instruction="reuse", operation="generate",
            resolved_profile_snapshot={}, model="local", parameters={},
            references={"input": [artifact.id]},
        )
        exported = tmp_path / "exported.zip"
        export_session(
            container.repositories, container.artifact_store, first.id, exported,
        )
        path = container.artifact_store.get(artifact.id)

        delete_session(container.repositories, first.id)
        assert exported.is_file() and path.is_file()
        assert artifact.id not in {
            item["id"] for item in
            collect(container.repositories, container.artifact_store)["candidates"]
        }
        with pytest.raises(KeyError):
            export_session(
                container.repositories, container.artifact_store, first.id,
                tmp_path / "deleted.zip",
            )

        delete_session(container.repositories, second.id)
        dry = collect(container.repositories, container.artifact_store)
        assert artifact.id in {item["id"] for item in dry["candidates"]}
        collect(
            container.repositories, container.artifact_store, confirmed=(artifact.id,),
        )
        assert not path.exists()
        assert exported.is_file()


def test_history_commands_are_wired_through_the_cli(tmp_path, png_bytes):
    workspace = tmp_path / "work"
    archive = tmp_path / "history.zip"
    with Container(workspace=str(workspace)) as container:
        session_id, turn_id, artifact_id = _seed(container, png_bytes)
        assert container.repositories.get("sessions", session_id)["name"] == "seed"

    code, forked = invoke(
        workspace, "session", "fork", session_id,
        "--from-turn", turn_id, "--artifact", artifact_id, "--name", "branch",
    )
    assert code == 0
    assert forked["data"]["session"]["origin_session_id"] == session_id

    code, exported = invoke(workspace, "session", "export", session_id, "--to", str(archive))
    assert code == 0 and archive.is_file()
    assert exported["data"]["artifacts"] == 1

    code, imported = invoke(workspace, "session", "import", str(archive))
    assert code == 0
    new_session = imported["data"]["session"]["id"]
    assert new_session != session_id

    code, dry = invoke(workspace, "asset", "gc", "--dry-run")
    assert code == 0 and dry["data"]["dry_run"] is True
    assert dry["data"]["candidates"] == []

    assert invoke(workspace, "session", "delete", session_id)[0] == 2
    assert invoke(workspace, "session", "delete", session_id, "--yes")[0] == 0
    with Container(workspace=str(workspace)) as container:
        assert container.repositories.get("sessions", session_id)["deleted_at"] is not None
    assert invoke(workspace, "session", "show", session_id)[0] == 4

    code, dry = invoke(workspace, "asset", "gc", "--dry-run")
    assert code == 0
    candidates = {item["id"] for item in dry["data"]["candidates"]}
    assert artifact_id in candidates
    assert invoke(workspace, "asset", "gc", "--yes")[0] == 2
    code, applied = invoke(
        workspace, "asset", "gc", "--plan", dry["data"]["plan"], "--yes",
    )
    assert code == 0
    assert artifact_id in applied["data"]["deleted"]

    assert invoke(workspace, "session", "export", new_session, "--to", str(archive))[0] == 5


def test_import_with_a_missing_native_context_and_unknown_origin_is_explicit(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path / "source")) as source:
        session_id, turn_id, _ = _seed(source, png_bytes)
        source.repositories.insert("native_contexts", {
            "id": new_id("ctx"), "provider": "openai", "family": "images",
            "profile_identity": "offline", "model": "local", "format_version": 1,
            "private_relpath": "context.bin", "sha256": "b" * 64,
            "expires_at": None, "availability": "available", "source_turn_id": turn_id,
        })
        archive = tmp_path / "history.zip"
        export_session(source.repositories, source.artifact_store, session_id, archive)

    with Container(workspace=str(tmp_path / "target")) as target:
        result = import_archive(target.repositories, target.artifact_store, archive)
        assert result["native_contexts_missing"][0]["availability"] == "missing"
        assert any(
            item["kind"] == "native_context_missing" for item in result["omissions"]
        )
