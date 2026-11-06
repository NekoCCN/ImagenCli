"""Local remove-ai-watermarks integration through an isolated interpreter."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from imagen_cli import settings
from imagen_cli.domain.primitive import new_id
from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.repositories import Repositories

SCHEMA_VERSION = 1
SUPPORTED_RANGE = ">=0.53,<0.54"
KNOWN_GOOD = "0.53.0"
PIPELINES = ("qwen-zimage", "qwen21-zimage", "sdxl-zimage", "chroma-zimage")
VISIBLE_BACKENDS = ("cv2", "migan", "lama")
# Optional dependency groups the isolated interpreter may install.
EXTRAS = (
    "visible", "migan", "lama", "qwen-zimage", "classify", "source-classify",
    "pixels", "detect", "heif", "trustmark", "diffusion",
)
DEFAULT_EXTRAS = "visible"
PIPELINE_LICENSES = {
    "qwen-zimage": "Apache-2.0 (Qwen-Image); model weights carry their own license",
    "qwen21-zimage": "Qwen Research License: non-commercial/personal use only",
    "sdxl-zimage": "CreativeML OpenRAIL++-M",
    "chroma-zimage": "Apache-2.0 (Chroma1)",
}

# Resident model bytes per documented pipeline, before offload/tiling effects.
ESTIMATE_GIB = {
    "qwen-zimage": 64,
    "qwen21-zimage": 64,
    "sdxl-zimage": 12,
    "chroma-zimage": 24,
}
OFFLOAD_FACTOR = 0.35
TILE_FACTOR = 1.25
BYTES_PER_PIXEL = 64
_LOCAL_RUN_LOCK = threading.Lock()


class WatermarkError(ValueError):
    def __init__(self, status: str, message: str, data: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.data = data or {}


def environment_root() -> Path:
    configured = os.environ.get("IMAGEN_RAIW_ROOT")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".local" / "share" / "imagen-cli" / "raiw"


def current_python() -> Path | None:
    configured = os.environ.get("IMAGEN_RAIW_PYTHON")
    if configured:
        candidate = Path(configured).expanduser()
        return candidate if candidate.is_file() else None
    root = environment_root() / "current"
    candidate = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return candidate if candidate.is_file() else None


def _imagen_version() -> str:
    from importlib import metadata

    try:
        return metadata.version("imagen-cli")
    except metadata.PackageNotFoundError:
        return "unknown"


def _invoke(python: Path, request: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    payload = {"schema_version": SCHEMA_VERSION, "imagen_cli_version": _imagen_version(), **request}
    try:
        result = subprocess.run(
            [str(python), "-I", "-m", "imagen_cli.remote.worker_cli"],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise WatermarkError(
            "unknown", "the isolated worker did not answer before the deadline"
        ) from None
    except OSError as error:
        raise WatermarkError(
            "missing", f"the isolated interpreter could not start: {error}"
        ) from error
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise WatermarkError(
            "failed", f"the isolated worker returned invalid JSON (exit {result.returncode})"
        ) from None
    if not isinstance(response, dict) or response.get("schema_version") != SCHEMA_VERSION:
        raise WatermarkError("incompatible_worker", "the isolated worker speaks another contract")
    if response.get("status") in {"failed", "incompatible_worker", "incompatible_raiw"}:
        detail = response.get("error") or {}
        raise WatermarkError(
            str(response["status"]), str(detail.get("message", "the isolated worker failed")),
            response,
        )
    return response


def run(request: dict[str, Any], *, timeout: float = 900) -> dict[str, Any]:
    """Send one job to the isolated interpreter; never import raiw in-process."""
    python = current_python()
    if python is None:
        raise WatermarkError(
            "missing",
            "the isolated raiw environment is not installed; run: "
            "imagen watermark setup --accept",
        )
    return _invoke(python, request, timeout=timeout)


def status() -> dict[str, Any]:
    python = current_python()
    limit = effective_limit()
    data: dict[str, Any] = {
        "interpreter": str(python) if python else None,
        "environment": str(environment_root()),
        "supported_range": SUPPORTED_RANGE,
        "known_good": KNOWN_GOOD,
        "memory_limit": limit,
        "memory_mb": None if limit is None else limit // 1024**2,
        "ready": False,
    }
    if python is None:
        data["missing"] = ["interpreter"]
        return data
    try:
        probe = run({"operation": "probe"}, timeout=120)
    except WatermarkError as error:
        data.update(status=error.status, error=str(error))
        return data
    data.update(
        ready=True,
        raiw_version=probe.get("raiw_version"),
        capabilities=probe.get("capabilities", {}),
        cuda=probe.get("cuda", False),
    )
    return data


def _total_memory_bytes() -> int | None:
    sysconf = getattr(os, "sysconf", None)
    if sysconf is None:  # Windows has no sysconf.
        return None
    try:
        return int(sysconf("SC_PAGE_SIZE") * sysconf("SC_PHYS_PAGES"))
    except (AttributeError, ValueError, OSError):
        return None


def effective_limit(memory_mb: int | None = None) -> int | None:
    if memory_mb is not None:
        return int(memory_mb) * 1024**2
    configured = settings.memory_limit()
    return configured if configured is not None else _total_memory_bytes()


def estimate_bytes(pipeline: str, pixels: int, *, tile: bool = False,
                   cpu_offload: bool = False) -> int:
    if pipeline not in ESTIMATE_GIB:
        raise ValueError("unknown watermark pipeline: " + pipeline)
    estimated = ESTIMATE_GIB[pipeline] * 1024**3
    if cpu_offload:
        estimated = int(estimated * OFFLOAD_FACTOR)
    if tile:
        estimated = int(estimated * TILE_FACTOR)
    return estimated + pixels * BYTES_PER_PIXEL


def budget_preflight(source_sha256: str, size: tuple[int, int], pipeline: str, *,
                     approval: str | None = None, memory_mb: int | None = None,
                     tile: bool = False, cpu_offload: bool = False) -> dict[str, Any]:
    from imagen_cli.application.postprocess_service import BudgetApprovalRequired

    pixels = size[0] * size[1]
    estimated = estimate_bytes(pipeline, pixels, tile=tile, cpu_offload=cpu_offload)
    limit = effective_limit(memory_mb)
    if limit is None or limit == 0 or estimated <= limit:
        return {"estimated_bytes": estimated, "limit": limit}
    payload = {
        "source_sha256": source_sha256, "pipeline": pipeline, "pixels": pixels,
        "tile": tile, "cpu_offload": cpu_offload, "estimated_bytes": estimated, "limit": limit,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    report = {
        "output_size": size,
        "output_pixels": pixels,
        "estimated_working_bytes": estimated,
        "limit_bytes": limit,
        "digest": digest,
        "reasons": ["estimated watermark memory exceeds the configured limit"],
    }
    if approval != digest:
        raise BudgetApprovalRequired(report)
    return {"estimated_bytes": estimated, "limit": limit, "budget_approval": digest}


_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
_VIDEO_EXTENSIONS = {
    "video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm",
    "video/x-matroska": ".mkv", "video/x-msvideo": ".avi", "video/x-flv": ".flv",
}


def _extras(value: str | None) -> str:
    named = [part.strip() for part in (value or DEFAULT_EXTRAS).split(",") if part.strip()]
    unknown = [part for part in named if part not in EXTRAS]
    if not named or unknown:
        raise ValueError(
            "unknown dependency extra: "
            + ", ".join(unknown or ["(none)"])
            + "; choose from " + ", ".join(EXTRAS)
        )
    return ",".join(dict.fromkeys(named))


def _package(extras: str, version_spec: str) -> str:
    return f"remove-ai-watermarks[{extras}]{version_spec}"


def _regions(values: list[str], size: tuple[int, int]) -> list[str]:
    width, height = size
    for raw in values:
        try:
            x, y, w, h = (int(part) for part in raw.replace(" ", "").split(","))
        except ValueError:
            raise ValueError(f"region must be x,y,width,height integers: {raw}") from None
        if w <= 0 or h <= 0:
            raise ValueError(f"region width/height must be positive: {raw}")
        if x < 0 or y < 0 or x + w > width or y + h > height:
            raise ValueError(
                f"region lies outside the {width}x{height} image: {raw}"
            )
    return list(values)


def _session(repositories: Repositories, artifact: str, session: str | None) -> str:
    from imagen_cli.storage import queries

    if session is not None:
        if repositories.get("sessions", session) is None:
            raise ValueError("destination session was not found")
        return session
    holders = queries.live_sessions(repositories)
    shared = queries.reference_sessions(repositories).get(artifact, set()) & holders
    if len(shared) != 1:
        raise ValueError("source artifact has no single live session; pass --session")
    return next(iter(shared))


def run_operation(
    repositories: Repositories,
    store: ArtifactStore,
    artifact: str,
    operation: str,
    *,
    session: str | None = None,
    pipeline: str | None = None,
    visible_backend: str | None = None,
    tile: bool = False,
    tile_size: int = 1024,
    cpu_offload: bool = False,
    max_resolution: int | None = None,
    force: bool = False,
    accept_license: bool = False,
    strength: float | None = None,
    timeout: float = 900,
    memory_mb: int | None = None,
    budget_approval: str | None = None,
    regions: list[str] | None = None,
    options: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    from PIL import Image

    row = repositories.get("artifacts", artifact)
    path = store.get(artifact)
    if row is None or path is None or row["save_state"] != "published":
        raise ValueError("watermark source must be a published artifact")
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != row["sha256"]:
        raise ValueError("source artifact bytes changed")
    video = str(row.get("media_type") or "").startswith("video/")
    video_operation = operation.startswith("watermark.video.")
    if video != video_operation:
        raise ValueError(
            "video artifacts need watermark video commands"
            if video else "that command only accepts video artifacts"
        )
    size = (int(row["width"]), int(row["height"]))
    if not video:
        with Image.open(path) as opened:
            size = opened.size
    read_only = operation in {"watermark.identify", "watermark.classify",
                              "watermark.video.identify"}
    # Resolve the destination before any expensive work or publication.
    session_id = None if read_only else _session(
        repositories, artifact, session,
    )
    extension = (_VIDEO_EXTENSIONS if video else _EXTENSIONS).get(
        row.get("media_type"), ".mp4" if video else ".png",
    )
    tuning = {key: value for key, value in (options or {}).items() if value is not None}
    params: dict[str, Any] = {}
    if operation == "watermark.classify":
        params["source"] = tuning.get("classify_source") is True
    elif operation == "watermark.video.identify":
        params["check_visible"] = True
    elif operation == "watermark.video.metadata":
        pass
    elif operation == "watermark.video.visible":
        for key in ("mark", "backend"):
            if key in tuning:
                params[key] = tuning[key]
    elif operation == "watermark.video.all":
        for key in ("mark", "backend", "include_invisible", "noise_std", "long_side",
                    "fps", "batch_size", "seed"):
            if key in tuning:
                params[key] = tuning[key]
    elif operation == "watermark.video.invisible":
        for key in ("noise_std", "long_side", "fps", "batch_size", "seed",
                    "model", "device"):
            if key in tuning:
                params[key] = tuning[key]
    elif operation == "watermark.erase":
        if not regions:
            raise ValueError("watermark erase needs at least one --region")
        params.update(
            regions=_regions(regions, size),
            backend=visible_backend or "cv2",
            inpaint_method=tuning.get("inpaint_method", "telea"),
            dilate=tuning.get("dilate", 3),
        )
    if operation in {"watermark.invisible", "watermark.all"}:
        pipeline = pipeline or "sdxl-zimage"
        if pipeline not in PIPELINES:
            raise ValueError("unknown watermark pipeline: " + pipeline)
        if not accept_license:
            raise WatermarkError(
                "approval_required",
                f"pipeline {pipeline} uses {PIPELINE_LICENSES[pipeline]}; "
                "rerun with --accept-license after reviewing it",
                {"license": {"pipeline": pipeline,
                             "license": PIPELINE_LICENSES[pipeline]}},
            )
        params.update(pipeline=pipeline, tile=tile, tile_size=tile_size,
                      cpu_offload=cpu_offload, force=force)
        if max_resolution is not None:
            params["max_resolution"] = max_resolution
        if strength is not None:
            params["strength"] = strength
        for key in ("vendor", "seed", "humanize", "unsharp",
                    "controlnet_conditioning_scale", "tile_overlap",
                    "adaptive_polish", "text_manifest", "fidelity_anchor"):
            if key in tuning:
                params[key] = tuning[key]
        if operation == "watermark.all":
            params["backend"] = visible_backend or tuning.get("backend", "auto")
            params["sensitivity"] = tuning.get("sensitivity", "auto")
        budget_preflight(digest, size, pipeline, approval=budget_approval,
                         memory_mb=memory_mb, tile=tile, cpu_offload=cpu_offload)
    if operation == "watermark.visible":
        if visible_backend is not None:
            params["backend"] = visible_backend
        for key in ("sensitivity", "mark"):
            if key in tuning:
                params[key] = tuning[key]
    exclusive = operation in {
        "watermark.visible", "watermark.invisible", "watermark.all",
        "watermark.video.visible", "watermark.video.all", "watermark.video.invisible",
    }
    if exclusive and not _LOCAL_RUN_LOCK.acquire(blocking=False):
        raise WatermarkError("conflict", "another local watermark run is active")
    try:
        with tempfile.TemporaryDirectory(prefix="imagen-watermark-") as directory:
            source = Path(directory) / ("input" + extension)
            source.write_bytes(data)
            output = (
                Path(directory) / ("output" + extension)
                if not read_only else None
            )
            request: dict[str, Any] = {
                "operation": operation, "input": str(source), "params": params,
            }
            if output is not None:
                request["output"] = str(output)
            response = run(request, timeout=timeout)
            if output is not None and not output.is_file():
                if operation == "watermark.video.visible" \
                        and response.get("status") == "no_watermark":
                    produced = None
                else:
                    raise WatermarkError(
                        "failed", "the worker did not produce an output image"
                    )
            else:
                produced = output.read_bytes() if output is not None else None
    finally:
        if exclusive:
            _LOCAL_RUN_LOCK.release()
    if read_only:
        return "succeeded", {"report": response.get("report", {}),
                             "raiw_version": response.get("raiw_version")}
    if produced is None:
        return "succeeded", {
            "status": response.get("status", "no_watermark"),
            "report": response.get("report", {}),
            "raiw_version": response.get("raiw_version"),
        }
    assert produced is not None
    assert session_id is not None
    identity = new_id("art")
    store.stage(identity, produced, origin_manifest={
        "kind": "watermark",
        "operation": operation,
        "source": artifact,
        "source_sha256": digest,
        "pipeline": pipeline,
        "raiw_version": response.get("raiw_version"),
        "status": response.get("status"),
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
        "status": response.get("status"),
        "report": response.get("report", {}),
        "raiw_version": response.get("raiw_version"),
    }


def batch_directory(
    directory: str | Path,
    *,
    to: str | Path | None = None,
    mode: str = "all",
    pipeline: str = "sdxl-zimage",
    backend: str = "auto",
    sensitivity: str = "auto",
    force: bool = False,
    accept_license: bool = False,
    options: dict[str, Any] | None = None,
    timeout: float = 3600,
) -> dict[str, Any]:
    """Process every image in one directory into a separate new directory."""
    source, target = _batch_paths(directory, to)
    if mode not in {"visible", "metadata", "all", "invisible"}:
        raise ValueError("unknown batch mode: " + mode)
    if mode in {"all", "invisible"} and not accept_license:
        raise WatermarkError(
            "approval_required",
            f"pipeline {pipeline} uses {PIPELINE_LICENSES[pipeline]}; "
            "rerun with --accept-license after reviewing it",
            {"license": {"pipeline": pipeline,
                         "license": PIPELINE_LICENSES[pipeline]}},
        )
    params = {
        "mode": mode, "pipeline": pipeline, "backend": backend,
        "sensitivity": sensitivity, "force": force,
        **{key: value for key, value in (options or {}).items() if value is not None},
    }
    response = run(
        {"operation": "watermark.batch", "directory": str(source),
         "output_directory": str(target), "params": params},
        timeout=timeout,
    )
    report = response.get("report", {})
    if not report.get("items"):
        raise ValueError("no supported images found in " + str(source))
    return {"source": str(source), "output": str(target), "report": report}


def video_batch_directory(
    directory: str | Path,
    *,
    to: str | Path | None = None,
    mode: str = "all",
    options: dict[str, Any] | None = None,
    timeout: float = 7200,
) -> dict[str, Any]:
    """Process every supported video in one directory into a separate directory."""
    source, target = _batch_paths(directory, to)
    if mode not in {"all", "visible", "metadata"}:
        raise ValueError("unknown video batch mode: " + mode)
    params = {"mode": mode, **{key: value for key, value in
                               (options or {}).items() if value is not None}}
    response = run(
        {"operation": "watermark.video.batch", "directory": str(source),
         "output_directory": str(target), "params": params},
        timeout=timeout,
    )
    report = response.get("report", {})
    if not report.get("items"):
        raise ValueError("no supported videos found in " + str(source))
    return {"source": str(source), "output": str(target), "report": report}


def _batch_paths(directory: str | Path, to: str | Path | None) -> tuple[Path, Path]:
    source = Path(directory).expanduser()
    if not source.is_dir():
        raise FileNotFoundError(str(source))
    target = (Path(to).expanduser() if to is not None
              else source.parent / (source.name + "_clean"))
    if target == source or source in target.parents:
        raise ValueError("the output directory must be outside the source directory")
    if target.exists() and not target.is_dir():
        raise ValueError("the output path is not a directory")
    if target.exists() and any(target.iterdir()):
        raise ValueError("the output directory is not empty")
    return source, target


def _provision(target: Path, package: str, *, accept: bool, extras: str) -> dict[str, Any]:
    """Create a fresh environment, verify it, then switch current atomically."""
    root = environment_root()
    version = _imagen_version()
    imagen_spec = os.environ.get("IMAGEN_RAIW_IMAGEN_SPEC", f"imagen-cli=={version}")
    plan: dict[str, Any] = {
        "environment": str(root / "current"),
        "target": str(target),
        "packages": [imagen_spec, package],
        "extras": extras,
        "installed": False,
    }
    if not accept:
        plan["hint"] = "rerun with --accept to create the isolated environment"
        return plan
    if target.exists():
        raise WatermarkError("conflict", "the target environment already exists")
    uv = shutil.which("uv")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if uv:
            subprocess.run([uv, "venv", str(target)], check=True, capture_output=True, timeout=600)
            python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            subprocess.run(
                [uv, "pip", "install", "--python", str(python), imagen_spec, package],
                check=True, capture_output=True, timeout=3600,
            )
        else:
            subprocess.run([sys.executable, "-m", "venv", str(target)],
                           check=True, capture_output=True, timeout=600)
            python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            subprocess.run(
                [str(python), "-m", "pip", "install", imagen_spec, package],
                check=True, capture_output=True, timeout=3600,
            )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as error:
        raise WatermarkError(
            "failed", f"environment setup failed ({type(error).__name__})"
        ) from None
    probe = _invoke(python, {"operation": "probe"}, timeout=300)
    link = root / "current"
    if link.is_symlink() or link.is_file():
        link.unlink()
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        # Windows without symlink privilege: point the override at the env.
        plan["environment"] = str(target)
        plan["hint"] = f"set IMAGEN_RAIW_PYTHON={python}"
    plan.update(
        installed=True, python=str(python),
        raiw_version=probe.get("raiw_version"),
        capabilities=probe.get("capabilities", {}),
    )
    return plan


def setup(accept: bool = False, extras: str | None = None) -> dict[str, Any]:
    """Preview or create the isolated environment; never upgrades in place."""
    selected = _extras(extras)
    return _provision(
        environment_root() / f"raiw-{KNOWN_GOOD}",
        _package(selected, SUPPORTED_RANGE), accept=accept, extras=selected,
    )


def update(accept: bool = False, to: str | None = None,
           extras: str | None = None) -> dict[str, Any]:
    """Install a new raiw environment and switch only after its probe passes."""
    if to is not None and not any(char.isdigit() for char in to):
        raise ValueError("--to must be a version such as 0.53.1")
    selected = _extras(extras)
    package = (_package(selected, f"=={to}") if to
               else _package(selected, SUPPORTED_RANGE))
    name = to or f"next-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    return _provision(environment_root() / f"raiw-{name}", package, accept=accept,
                      extras=selected)
