"""Read-only signal comparison between two published artifacts."""

from __future__ import annotations

import math
from typing import Any

from PIL import Image

from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.repositories import Repositories


def compare(
    repositories: Repositories, store: ArtifactStore, first: str, second: str
) -> dict[str, Any]:
    try:
        from imagen_cli.application import filters_numpy
    except ImportError as error:
        raise ValueError("compare requires the postprocess extra") from error
    rows = []
    for identity in (first, second):
        row = repositories.get("artifacts", identity)
        if row is None:
            raise KeyError(identity)
        if row["save_state"] != "published":
            raise ValueError("artifact is not published")
        rows.append(row)
    paths = [store.get(identity) for identity in (first, second)]
    if any(path is None for path in paths):
        raise ValueError("artifact bytes are unavailable")
    assert paths[0] is not None and paths[1] is not None
    with Image.open(paths[0]) as one, Image.open(paths[1]) as two:
        one.load()
        two.load()
        if one.size != two.size:
            raise ValueError("artifacts have different dimensions")
        score = filters_numpy.psnr(one, two)
        similarity = filters_numpy.ssim(one, two)
    return {
        "first": first,
        "second": second,
        "width": rows[0]["width"],
        "height": rows[0]["height"],
        "identical": math.isinf(score),
        "psnr": None if math.isinf(score) else round(score, 4),
        "ssim": round(similarity, 6),
        "ssim_metric": "local_gaussian_pillow_l_v1",
    }
