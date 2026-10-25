"""Release manifests are hash-verifiable and keep blockers explicit."""

import hashlib
import warnings
from importlib.metadata import PathDistribution

from imagen_cli.application import release_readiness
from imagen_cli.application.release_readiness import manifest


def test_dependency_report_handles_missing_metadata_without_warnings(tmp_path, monkeypatch):
    distributions = []
    for index, contents in enumerate((
        "Name: no-license\nVersion: 1.0\n",
        "Version: 2.0\n",
        "Name: licensed\nVersion: 3.0\nLicense: MIT\n",
    )):
        directory = tmp_path / f"package-{index}.dist-info"
        directory.mkdir()
        (directory / "METADATA").write_text(contents, encoding="utf-8")
        distributions.append(PathDistribution(directory))
    monkeypatch.setattr(release_readiness.metadata, "distributions", lambda: distributions)
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        report = release_readiness.dependency_report()
    assert report == [
        {"name": "licensed", "version": "3.0", "license": "MIT"},
        {"name": "no-license", "version": "1.0", "license": "UNKNOWN"},
    ]


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
