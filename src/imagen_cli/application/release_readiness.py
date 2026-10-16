"""Release manifest: artifact hashes, dependency licenses and explicit blockers."""

import hashlib
from importlib import metadata
from pathlib import Path
from typing import Any

__all__ = ["dependency_report", "manifest"]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dependency_report() -> list[dict[str, Any]]:
    report = []
    for distribution in metadata.distributions():
        name = distribution.metadata["Name"]
        if not name:
            continue
        report.append({
            "name": name,
            "version": distribution.version,
            "license": distribution.metadata["License"] or "UNKNOWN",
        })
    return sorted(report, key=lambda item: item["name"].casefold())


def manifest(
    dist: Path, *, live_evidence: list[str] | None = None,
    platforms: list[str] | None = None,
) -> dict[str, Any]:
    artifacts = []
    for path in sorted(dist.glob("*")):
        if path.is_file() and path.name != "SHA256SUMS":
            artifacts.append({
                "name": path.name, "sha256": _sha256(path),
                "byte_size": path.stat().st_size,
            })
    blockers: list[str] = []
    if not artifacts:
        blockers.append("no distribution artifacts")
    if not live_evidence:
        blockers.append("no authorized live provider evidence")
    if not platforms:
        blockers.append("no cross-platform install evidence")
    return {
        "schema_version": 1,
        "artifacts": artifacts,
        "dependencies": dependency_report(),
        "blockers": blockers,
    }
