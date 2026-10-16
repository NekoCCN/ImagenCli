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
