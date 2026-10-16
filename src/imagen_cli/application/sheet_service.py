"""Contact sheet: one new artifact built from several published artifacts."""

from __future__ import annotations

import io
import math
from datetime import UTC, datetime
from typing import Any

from PIL import Image

from imagen_cli.domain.primitive import new_id
from imagen_cli.storage import queries
from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.repositories import Repositories

BACKGROUND = (24, 24, 24)
GAP = 8


def _session(repositories: Repositories, sources: list[str], session: str | None) -> str:
    holders = queries.live_sessions(repositories)
    if session is not None:
        if repositories.get("sessions", session) is None:
            raise KeyError(session)
        return session
    shared: set[str] | None = None
    ownership = queries.reference_sessions(repositories)
    for identity in sources:
        current = ownership.get(identity, set()) & holders
        shared = current if shared is None else shared & current
    if not shared:
        raise ValueError("source artifacts have no shared session; pass --session")
    if len(shared) > 1:
        raise ValueError("source artifacts belong to several sessions; pass --session")
    return next(iter(shared))


def build(
    repositories: Repositories,
    store: ArtifactStore,
    sources: list[str],
    *,
    session: str | None = None,
    columns: int = 3,
    cell: int = 256,
) -> dict[str, Any]:
    if not 1 <= len(sources) <= 100:
        raise ValueError("a sheet needs between 1 and 100 artifacts")
    if len(set(sources)) != len(sources):
        raise ValueError("duplicate sheet source")
    if not 1 <= columns <= 10 or not 32 <= cell <= 2048:
        raise ValueError("invalid sheet geometry")
    owner = _session(repositories, sources, session)
    images: list[Image.Image] = []
    digests: list[dict[str, Any]] = []
    for identity in sources:
        row = repositories.get("artifacts", identity)
        if row is None or row["save_state"] != "published":
            raise ValueError("sheet sources must be published artifacts")
        path = store.get(identity)
        if path is None:
            raise ValueError("sheet source bytes are unavailable")
        with Image.open(path) as opened:
            opened.load()
            images.append(opened.convert("RGB"))
        digests.append({"artifact": identity, "sha256": row["sha256"]})
    rows = math.ceil(len(images) / columns)
    width = columns * cell + (columns + 1) * GAP
    height = rows * cell + (rows + 1) * GAP
    canvas = Image.new("RGB", (width, height), BACKGROUND)
    for index, image in enumerate(images):
        thumbnail = image.copy()
        thumbnail.thumbnail((cell, cell), Image.Resampling.LANCZOS)
        row, column = divmod(index, columns)
        left = GAP + column * (cell + GAP) + (cell - thumbnail.width) // 2
        top = GAP + row * (cell + GAP) + (cell - thumbnail.height) // 2
        canvas.paste(thumbnail, (left, top))
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG", optimize=True)
    identity = new_id("art")
    store.stage(
        identity, buffer.getvalue(),
        origin_manifest={
            "kind": "sheet", "sources": digests, "columns": columns, "cell": cell,
            "deterministic": True,
        },
    )
    store.publish(identity)
    repositories.insert(
        "artifact_links",
        {
            "session_id": owner, "artifact_id": identity, "role": "output",
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        },
    )
    return {"artifact": repositories.get("artifacts", identity), "sources": digests}
