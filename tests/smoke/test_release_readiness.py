"""Release manifests are hash-verifiable and keep blockers explicit."""

import hashlib

from imagen_cli.application.release_readiness import manifest


def test_manifest_hashes_every_artifact(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "pkg-1.0.whl").write_bytes(b"wheel")
    (dist / "SHA256SUMS").write_text("ignored")
    value = manifest(dist, live_evidence=["local gateway"], platforms=["linux"])
    assert [item["name"] for item in value["artifacts"]] == ["pkg-1.0.whl"]
    assert value["artifacts"][0]["sha256"] == hashlib.sha256(b"wheel").hexdigest()
    assert value["blockers"] == []
    assert any(item["name"] for item in value["dependencies"])


def test_missing_evidence_is_an_explicit_blocker(tmp_path):
    empty = tmp_path / "dist"
    empty.mkdir()
    value = manifest(empty)
    assert value["artifacts"] == []
    assert len(value["blockers"]) == 3
    assert "no authorized live provider evidence" in value["blockers"]
