"""Two-phase local artifact publication; recovery never creates remote work."""

import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from PIL import Image, UnidentifiedImageError

from imagen_cli.storage.repositories import Repositories
from imagen_cli.storage.safety import fsync_directory, safe_path, secure_directory, storage_id

MAX_BYTES = 64 * 1024 * 1024
MAX_VIDEO_BYTES = 1024 * 1024 * 1024
MAX_PIXELS = 100_000_000

_IMAGE_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1", b"avif", b"avis"}
_VIDEO_SUFFIX = {
    "video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm",
    "video/x-matroska": ".mkv", "video/x-msvideo": ".avi", "video/x-flv": ".flv",
}


def looks_like_video(data: bytes) -> bool:
    """Container sniffing that leaves HEIF/AVIF images to Pillow."""
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return data[8:12] not in _IMAGE_BRANDS
    return (
        data.startswith(b"\x1aE\xdf\xa3")          # EBML: Matroska or WebM
        or (data.startswith(b"RIFF") and data[8:12] == b"AVI ")
        or data.startswith(b"FLV")
    )


def _video_type(data: bytes) -> str:
    if data.startswith(b"RIFF"):
        return "video/x-msvideo"
    if data.startswith(b"FLV"):
        return "video/x-flv"
    if data.startswith(b"\x1aE\xdf\xa3"):
        return "video/webm" if b"webm" in data[:64] else "video/x-matroska"
    return "video/quicktime" if data[8:12] == b"qt  " else "video/mp4"


def _video_metadata(data: bytes) -> dict[str, Any]:
    executable = shutil.which("ffprobe")
    if executable is None:
        raise ValueError("video artifacts require ffprobe on PATH")
    media_type = _video_type(data)
    fd, name = tempfile.mkstemp(suffix=_VIDEO_SUFFIX[media_type])
    os.close(fd)
    try:
        Path(name).write_bytes(data)
        try:
            completed = subprocess.run(
                [executable, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "json", name],
                capture_output=True, text=True, timeout=120, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValueError("invalid video") from error
    finally:
        Path(name).unlink(missing_ok=True)
    if completed.returncode != 0:
        raise ValueError("invalid video")
    try:
        streams = json.loads(completed.stdout).get("streams") or []
        width, height = int(streams[0]["width"]), int(streams[0]["height"])
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("video has no readable video stream") from error
    if width <= 0 or height <= 0:
        raise ValueError("video has no readable video stream")
    return {
        "width": width,
        "height": height,
        "media_type": media_type,
        "byte_size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


_IMAGE_LIMIT_LOCK = RLock()


@contextmanager
def image_limits(allow_large: bool = False) -> Iterator[None]:
    """Serialize Pillow's global bomb threshold while inspecting confirmed local images."""
    with _IMAGE_LIMIT_LOCK:
        previous = Image.MAX_IMAGE_PIXELS
        if allow_large:
            Image.MAX_IMAGE_PIXELS = None
        try:
            yield
        finally:
            Image.MAX_IMAGE_PIXELS = previous


def inspect_media(
    data: bytes, *, max_pixels: int | None = None, max_bytes: int | None = None,
    allow_large: bool = False,
) -> dict[str, Any]:
    max_pixels = MAX_PIXELS if max_pixels is None else max_pixels
    max_bytes = MAX_BYTES if max_bytes is None else max_bytes
    if looks_like_video(data):
        if not data or len(data) > MAX_VIDEO_BYTES:
            raise ValueError("media byte limit exceeded")
        return _video_metadata(data)
    if not data or len(data) > max_bytes:
        raise ValueError("media byte limit exceeded")
    try:
        with image_limits(allow_large), warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                if width * height > max_pixels:
                    raise ValueError("image pixel limit exceeded")
                media_type = Image.MIME.get(image.format or "")
                if not media_type:
                    raise ValueError("unknown image media type")
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as error:
        raise ValueError("invalid image") from error
    return {
        "width": width,
        "height": height,
        "media_type": media_type,
        "byte_size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


class ArtifactStore:
    def __init__(self, root: Path, repositories: Repositories) -> None:
        self.root = secure_directory(root)
        self.repositories = repositories

    def stage(
        self,
        identity: str,
        data: bytes,
        *,
        producer_turn_id: str | None = None,
        producer_job_id: str | None = None,
        origin_manifest: dict[str, Any] | None = None,
        allow_large: bool = False,
    ) -> dict[str, Any]:
        storage_id(identity)
        approved = allow_large
        pixels = (origin_manifest or {}).get("budget", {}).get("output_pixels", MAX_PIXELS)
        metadata = inspect_media(data, max_pixels=pixels if approved else MAX_PIXELS,
                                 max_bytes=max(MAX_BYTES, len(data)) if approved else MAX_BYTES,
                                 allow_large=approved)
        if approved:
            origin_manifest = {**(origin_manifest or {}), "local_media_limits": {
                "pixels": metadata["width"] * metadata["height"], "bytes": len(data),
            }}
        if self.repositories.get("artifacts", identity) is not None:
            raise FileExistsError(identity)
        name = identity + ".staging"
        temporary = safe_path(self.root, name)
        target_name = identity + ".image"
        target = safe_path(self.root, target_name)
        if target.exists():
            raise FileExistsError(target)
        # Persist expected facts before any bytes so interrupted writes are reconcilable.
        row = {
            "id": identity,
            "producer_turn_id": producer_turn_id,
            "producer_job_id": producer_job_id,
            "managed_relpath": target_name,
            "staging_relpath": name,
            "save_state": "staging",
            **metadata,
            "origin_manifest": origin_manifest or {"kind": "local"},
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        self.repositories.insert("artifacts", row)
        fd = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        fsync_directory(self.root)
        return row

    def _verified_bytes(self, row: dict[str, Any], path: Path) -> bytes:
        manifest = row.get("origin_manifest") or {}
        approved = bool(manifest.get("kind") == "postprocess" and manifest.get("budget_approval"))
        limits = manifest.get("local_media_limits", {}) if approved else {}
        base = MAX_VIDEO_BYTES if str(row.get("media_type") or "").startswith("video/") \
            else MAX_BYTES
        max_bytes = max(base, limits.get("bytes", base))
        with path.open("rb") as source:
            data = source.read(max_bytes + 1)
        actual = inspect_media(data, max_pixels=max(MAX_PIXELS, limits.get("pixels", MAX_PIXELS)),
                               max_bytes=max_bytes, allow_large=approved)
        if any(row.get(key) != value for key, value in actual.items()):
            raise ValueError("artifact metadata mismatch")
        return data

    def _finish(self, row: dict[str, Any], target: Path) -> Any:
        self._verified_bytes(row, target)
        # Windows rejects fsync on a read-only handle; open for update instead.
        with target.open("rb+") as output:
            os.fsync(output.fileno())
        fsync_directory(self.root)
        with self.repositories.transaction():
            self.repositories.update("artifacts", row["id"], {"save_state": "published"})
        temporary = safe_path(self.root, row["staging_relpath"])
        temporary.unlink(missing_ok=True)
        fsync_directory(self.root)
        return self.repositories.get("artifacts", row["id"])

    def publish(self, identity: str) -> Any:
        row = self.repositories.get("artifacts", identity, include_private=True)
        if row is None:
            raise KeyError(identity)
        if row["save_state"] == "published":
            return row
        if row["save_state"] != "staging":
            raise ValueError("artifact is not staging")
        source = safe_path(self.root, row["staging_relpath"])
        target = safe_path(self.root, row["managed_relpath"])
        data = self._verified_bytes(row, source)
        fd = os.open(
            target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        return self._finish(row, target)

    def reconcile(self, identity: str) -> Any:
        row = self.repositories.get("artifacts", identity, include_private=True)
        if row is None:
            raise KeyError(identity)
        if row["save_state"] == "published":
            return row
        if row["save_state"] == "save_failed":
            return row
        target = safe_path(self.root, row["managed_relpath"])
        try:
            if target.exists():
                return self._finish(row, target)
            return self.publish(identity)
        except (OSError, ValueError):
            self.repositories.update("artifacts", identity, {"save_state": "save_failed"})
            return self.repositories.get("artifacts", identity)

    def get(self, identity: str) -> Path | None:
        row = self.repositories.get("artifacts", identity)
        if row is None or row["save_state"] != "published":
            return None
        path = safe_path(self.root, row["managed_relpath"])
        self._verified_bytes(row, path)
        return path
