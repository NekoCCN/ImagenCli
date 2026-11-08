"""Downscale then upscale: rebuild the pixels instead of running a diffusion model.

This is the cheap removal path. It never imports `remove_ai_watermarks`, never
needs an isolated environment, and never claims the watermark is gone: it
rewrites pixel statistics, so the report carries ``verified: false``.
"""

from __future__ import annotations

import hashlib
import io
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image

from imagen_cli.application import superres, watermark_service
from imagen_cli.domain.primitive import new_id

SUPPORTED_MODES = frozenset({"RGB", "RGBA", "L", "LA"})
DEFAULT_MODEL = "realesrgan-x2plus"
DEFAULT_TILE = 256
DEFAULT_OVERLAP = 16


def plan_size(size: tuple[int, int], model: str, factor: float | None) -> dict[str, Any]:
    """Return the three sizes this run will move through, never silently guessed."""
    if model not in superres.MANIFEST:
        raise ValueError("unknown super-resolution model: " + model)
    scale = int(superres.MANIFEST[model]["scale"])
    chosen = float(factor) if factor is not None else 1 / scale
    if not 0 < chosen <= 1:
        raise ValueError("factor must be greater than 0 and at most 1")
    width, height = int(size[0]), int(size[1])
    intermediate = [max(1, round(width * chosen)), max(1, round(height * chosen))]
    return {
        "input_size": [width, height],
        "intermediate_size": intermediate,
        "output_size": [intermediate[0] * scale, intermediate[1] * scale],
        "factor": chosen,
        "scale": scale,
    }


def run(
    repositories: Any,
    store: Any,
    artifact: str,
    *,
    session: str | None = None,
    model: str = DEFAULT_MODEL,
    factor: float | None = None,
    backend: str | None = None,
    tile: int = DEFAULT_TILE,
    overlap: int = DEFAULT_OVERLAP,
    budget_approval: str | None = None,
    runner: Any = None,
    progress: Any = None,
) -> tuple[str, dict[str, Any]]:
    from imagen_cli.application.postprocess_service import budget_preflight

    row = repositories.get("artifacts", artifact)
    path = store.get(artifact)
    if row is None or path is None or row["save_state"] != "published":
        raise ValueError("watermark source must be a published artifact")
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != row["sha256"]:
        raise ValueError("source artifact bytes changed")

    if not superres.weights_path(model).is_file():
        raise watermark_service.WatermarkError(
            "missing",
            "the super-resolution weights are not installed; run: "
            f"imagen superres fetch --model {model} --accept",
        )
    with Image.open(path) as opened:
        opened.load()
        size = opened.size
        mode = opened.mode
        profile = opened.info.get("icc_profile")
        if mode not in SUPPORTED_MODES:
            raise ValueError(f"{mode} color mode is not supported by watermark resample")
        plan = plan_size(size, model, factor)
        source_image = opened.convert("RGBA" if "A" in mode else "RGB")

    # Resolve the destination before any expensive work or publication.
    session_id = watermark_service._session(repositories, artifact, session)
    budget = budget_preflight(
        Path(path),
        [
            {"op": "linear_resize", "params": {"scale": plan["factor"]}},
            {"op": "superres", "params": {"model": model}},
        ],
        budget_approval,
    )

    lock = watermark_service._LOCAL_RUN_LOCK
    if not lock.acquire(blocking=False):
        raise watermark_service.WatermarkError(
            "conflict", "another local watermark run is active"
        )
    try:
        produced, engine = _render(
            source_image, mode, plan, model=model, backend=backend,
            tile=tile, overlap=overlap, runner=runner, progress=progress,
        )
    finally:
        lock.release()

    buffer = io.BytesIO()
    # Dropping an embedded profile would silently change the colour space.
    options: dict[str, Any] = {"format": "PNG"}
    if profile:
        options["icc_profile"] = profile
    produced.save(buffer, **options)
    identity = new_id("art")
    store.stage(identity, buffer.getvalue(), origin_manifest={
        "kind": "watermark",
        "operation": "watermark.resample",
        "source": artifact,
        "source_sha256": digest,
        "model": model,
        "factor": plan["factor"],
        "scale": plan["scale"],
        "backend": backend,
        "provider_used": engine.get("provider"),
        "tile": engine.get("tile", tile),
        "overlap": engine.get("overlap", overlap),
        "verified": False,
    })
    store.publish(identity)
    repositories.insert("artifact_links", {
        "session_id": session_id,
        "artifact_id": identity, "role": "output",
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    })
    return "succeeded", {
        "artifact": repositories.get("artifacts", identity),
        "source": artifact,
        "report": {
            "plan": plan,
            "engine": engine,
            "mode": mode,
            "budget_approval": budget.get("budget_approval"),
            "verified": False,
        },
    }


def _render(
    image: Image.Image,
    mode: str,
    plan: dict[str, Any],
    *,
    model: str,
    backend: str | None,
    tile: int,
    overlap: int,
    runner: Any,
    progress: Any,
) -> tuple[Image.Image, dict[str, Any]]:
    intermediate = image.resize(
        tuple(plan["intermediate_size"]), Image.Resampling.LANCZOS
    )
    try:
        produced, engine = superres.upscale(
            intermediate, model=model, backend=backend, tile=tile, overlap=overlap,
            accept=False, runner=runner, progress=progress,
        )
    except FileNotFoundError as error:
        raise watermark_service.WatermarkError(
            "missing",
            f"the super-resolution weights are not installed; run: "
            f"imagen superres fetch --model {model} --accept",
        ) from error
    if mode in {"L", "LA"}:
        # Keep the source's colour space instead of quietly promoting it to RGB.
        preserved = produced.convert("LA" if mode == "LA" else "L")
        return preserved, engine
    return produced, engine
