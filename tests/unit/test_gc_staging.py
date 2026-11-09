"""asset gc removes staging files, not only published ones."""

from pathlib import Path

from imagen_cli.application.cleanup_service import collect
from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.database import connect
from imagen_cli.storage.repositories import Repositories


def test_gc_unlinks_staging_file(tmp_path: Path, png_bytes: bytes):
    root = tmp_path / "workspace"
    root.mkdir()
    repositories = Repositories(connect(root / "state.sqlite3"))
    store = ArtifactStore(root / "artifacts", repositories)
    identity = "art_" + "e" * 32
    store.stage(identity, png_bytes)
    staging = root / "artifacts" / f"{identity}.staging"
    assert staging.exists()

    plan = collect(repositories, store)
    result = collect(repositories, store, confirmed=(identity,))

    assert plan["candidates"][0]["id"] == identity
    assert result["deleted"] == [identity]
    assert not staging.exists()
    repositories.connection.close()


def test_gc_preserves_remote_job_audit_after_reclaiming_its_artifacts(
    tmp_path: Path,
    png_bytes: bytes,
):
    root = tmp_path / "workspace"
    root.mkdir()
    repositories = Repositories(connect(root / "state.sqlite3"))
    store = ArtifactStore(root / "artifacts", repositories)
    source = "art_" + "a" * 32
    output = "art_" + "b" * 32
    store.stage(source, png_bytes)
    store.publish(source)
    store.stage(output, png_bytes)
    store.publish(output)
    repositories.insert(
        "remote_jobs",
        {
            "id": "rmt_gc",
            "provider": "modal",
            "endpoint_host": "worker.example",
            "operation": "matte",
            "input_artifact_id": source,
            "input_sha256": "a" * 64,
            "request_canonical": "{}",
            "job_digest": "b" * 64,
            "max_seconds": 60,
            "status": "succeeded",
            "artifact_id": output,
            "created_at": "2026-11-09T00:00:00Z",
            "updated_at": "2026-11-09T00:00:00Z",
        },
    )

    plan = collect(repositories, store)
    result = collect(repositories, store, confirmed=(source, output))

    assert {row["id"] for row in plan["candidates"]} == {source, output}
    assert result["deleted"] == [source, output]
    assert repositories.get("remote_jobs", "rmt_gc")["input_artifact_id"] == source
    assert repositories.get("remote_jobs", "rmt_gc")["artifact_id"] == output
    assert repositories.get("artifacts", source) is None
    assert repositories.get("artifacts", output) is None
    repositories.connection.close()
