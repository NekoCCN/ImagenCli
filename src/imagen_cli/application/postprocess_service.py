"""Local post-processing: a new artifact per pipeline, never an overwrite."""

from __future__ import annotations

from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import PIL
from PIL import Image

from imagen_cli.application import media_ops
from imagen_cli.domain.primitive import new_id
from imagen_cli.storage import queries
from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.repositories import Repositories

MAX_STEPS = 20


def _engine() -> dict[str, str]:
    try:
        package = version("imagen-cli")
    except PackageNotFoundError:  # pragma: no cover - editable source trees
        package = "0.0.0"
    return {"imagen_cli": package, "pillow": PIL.__version__}


def validate(pipeline: Any) -> list[dict[str, Any]]:
    if not isinstance(pipeline, list) or not pipeline:
        raise ValueError("pipeline must be a non-empty list")
    if len(pipeline) > MAX_STEPS:
        raise ValueError("pipeline exceeds the step limit")
    steps: list[dict[str, Any]] = []
    for step in pipeline:
        if not isinstance(step, dict) or not isinstance(step.get("op"), str):
            raise ValueError("each step needs an op")
        name = step["op"]
        if name not in media_ops.OPS:
            raise ValueError(f"unknown operation: {name}")
        params = step.get("params", {})
        if not isinstance(params, dict):
            raise ValueError("operation params must be an object")
        extra = set(step) - {"op", "params"}
        if extra:
            raise ValueError("unexpected step field")
        steps.append({"op": name, "params": dict(params)})
    return steps


def _owner(repositories: Repositories, source: str) -> str:
    live = queries.live_sessions(repositories)
    holders = queries.reference_sessions(repositories).get(source, set()) & live
    if len(holders) == 1:
        return next(iter(holders))
    if not holders:
        raise ValueError("source artifact has no session; pass --session")
    raise ValueError("source artifact belongs to several sessions; pass --session")


def apply(
    repositories: Repositories,
    store: ArtifactStore,
    source: str,
    pipeline: Any,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    steps = validate(pipeline)
    row = repositories.get("artifacts", source, include_private=True)
    if row is None:
        raise KeyError(source)
    if row["save_state"] != "published":
        raise ValueError("source artifact is not published")
    path = store.get(source)
    if path is None:
        raise ValueError("source artifact bytes are unavailable")
    session = session_id or _owner(repositories, source)
    if repositories.get("sessions", session) is None:
        raise KeyError(session)
    with Image.open(path) as opened:
        opened.load()
        current = opened.convert("RGBA" if "A" in opened.getbands() else "RGB")
    for step in steps:
        operation = media_ops.OPS[step["op"]]
        try:
            current = operation(current, **step["params"])
        except TypeError as error:
            raise ValueError(f"invalid parameters for {step['op']}") from error
    data = media_ops.encode(current)
    identity = new_id("art")
    manifest = {
        "kind": "postprocess",
        "source_artifact": source,
        "source_sha256": row["sha256"],
        "pipeline": steps,
        "engine": _engine(),
        "deterministic": True,
        "seed": None,
    }
    store.stage(identity, data, origin_manifest=manifest)
    store.publish(identity)
    repositories.insert(
        "artifact_links",
        {
            "session_id": session, "artifact_id": identity, "role": "output",
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        },
    )
    return repositories.get("artifacts", identity) or {}
