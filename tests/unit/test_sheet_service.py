"""Contact sheets: one new artifact linked to the shared session."""

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from imagen_cli.application import sheet_service
from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.database import connect
from imagen_cli.storage.repositories import Repositories


def png(colour: str, size: tuple[int, int] = (10, 10)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def workspace(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    repositories = Repositories(connect(root / "state.sqlite3"))
    store = ArtifactStore(root / "artifacts", repositories)
    repositories.insert(
        "sessions", {"id": "ses_a", "name": "a", "version": 0,
                     "created_at": "2026-10-24T00:00:00Z"}
    )
    identities = []
    for seed, colour in (("a", "red"), ("b", "blue"), ("c", "green")):
        identity = "art_" + seed * 32
        store.stage(identity, png(colour))
        store.publish(identity)
        repositories.insert("artifact_links", {
            "session_id": "ses_a", "artifact_id": identity, "role": "output",
            "created_at": "2026-10-24T00:00:00Z",
        })
        identities.append(identity)
    yield repositories, store, identities
    repositories.connection.close()


def test_sheet_is_linked_and_sized(workspace):
    repositories, store, identities = workspace
    result = sheet_service.build(repositories, store, identities, columns=2, cell=64)
    artifact = result["artifact"]
    assert artifact["width"] == 2 * 64 + 3 * sheet_service.GAP
    assert artifact["height"] == 2 * 64 + 3 * sheet_service.GAP
    assert [item["artifact"] for item in result["sources"]] == identities
    links = repositories.list("artifact_links", session_id="ses_a")
    assert artifact["id"] in {row["artifact_id"] for row in links}


def test_sheet_refuses_duplicates_and_mixed_ownership(workspace):
    repositories, store, identities = workspace
    with pytest.raises(ValueError, match="duplicate"):
        sheet_service.build(repositories, store, [identities[0], identities[0]])
    repositories.insert(
        "sessions", {"id": "ses_b", "name": "b", "version": 0,
                     "created_at": "2026-10-24T00:00:00Z"}
    )
    repositories.insert("artifact_links", {
        "session_id": "ses_b", "artifact_id": identities[0], "role": "reference",
        "created_at": "2026-10-24T00:00:00Z",
    })
    with pytest.raises(ValueError, match="several sessions"):
        sheet_service.build(repositories, store, [identities[0]])
    result = sheet_service.build(
        repositories, store, [identities[0], identities[1]], session="ses_b"
    )
    assert result["artifact"]["width"] > 0
