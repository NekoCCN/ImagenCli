"""Two-phase local artifact publication; recovery never creates remote work."""

import hashlib
import io
import os
import warnings
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from imagen_cli.storage.repositories import Repositories
from imagen_cli.storage.safety import fsync_directory, safe_path, secure_directory, storage_id

MAX_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 100_000_000


def inspect_media(data: bytes) -> dict[str, Any]:
    if not data or len(data) > MAX_BYTES:
        raise ValueError("image byte limit exceeded")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                if width * height > MAX_PIXELS:
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
    ) -> dict[str, Any]:
        storage_id(identity)
        metadata = inspect_media(data)
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
        with path.open("rb") as source:
            data = source.read(MAX_BYTES + 1)
        actual = inspect_media(data)
        if any(row.get(key) != value for key, value in actual.items()):
            raise ValueError("artifact metadata mismatch")
        return data

    def _finish(self, row: dict[str, Any], target: Path) -> Any:
        self._verified_bytes(row, target)
        with target.open("rb") as output:
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
