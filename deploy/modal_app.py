"""Modal adapter: the same worker image as Salad, mounted on a Modal Volume.

Deploy only after explicit authorization:
    modal deploy deploy/modal_app.py

Required Modal secret:
    imagen-worker-auth  -> IMAGEN_WORKER_TOKEN

Optional Modal secret (set IMAGEN_HF_SECRET=imagen-hf when models are gated):
    imagen-hf           -> HF_TOKEN
"""

from __future__ import annotations

import os
import sys
from typing import Any

import modal

WORKER_IMAGE = os.environ.get(
    "IMAGEN_WORKER_IMAGE", "ghcr.io/nekoccn/imagen-worker:0.1.17-raiw0.53.0"
)

GPU = os.environ.get("IMAGEN_MODAL_GPU", "L4")
MEMORY_MB = int(os.environ.get("IMAGEN_MODAL_MEMORY_MB", "2048"))
CPU = float(os.environ.get("IMAGEN_MODAL_CPU", "1"))

# The container reads IMAGEN_MODAL_GPU only to report the card in /v1/meta.
image = modal.Image.from_registry(WORKER_IMAGE).env({"IMAGEN_MODAL_GPU": GPU})
app = modal.App("imagen-worker")

hf_cache = modal.Volume.from_name("imagen-hf-cache", create_if_missing=True)
jobs = modal.Volume.from_name("imagen-jobs", create_if_missing=True)

secrets = [modal.Secret.from_name("imagen-worker-auth")]
if hf_secret := os.environ.get("IMAGEN_HF_SECRET"):
    secrets.append(modal.Secret.from_name(hf_secret))


@app.function(  # type: ignore[untyped-decorator]
    image=image,
    gpu=GPU,
    cpu=CPU,
    memory=MEMORY_MB,
    timeout=7200,
    max_containers=1,
    volumes={"/root/.cache/huggingface": hf_cache, "/jobs": jobs},
    secrets=secrets,
)
@modal.asgi_app()  # type: ignore[untyped-decorator]
def web() -> Any:
    sys.path.insert(0, "/app")
    from app import from_environment  # type: ignore[import-not-found]

    return from_environment()
