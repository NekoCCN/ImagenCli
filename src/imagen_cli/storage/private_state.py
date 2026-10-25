"""Opaque private state: fail closed without verified current-user permissions."""

import hashlib
import json
import os
import re
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from imagen_cli.storage.safety import (
    _WINDOWS,
    _WindowsSecurity,
    fsync_directory,
    safe_path,
    secure_directory,
    storage_id,
)

MAX_SECRET_BYTES = 64 * 1024


def credential_name(profile_id: str) -> str:
    return "cred-" + hashlib.sha256(profile_id.encode("utf-8")).hexdigest()[:24]


class PrivateStateStore:
    def __init__(self, root: Path):
        self.root = secure_directory(root)

    @contextmanager
    def _directory(self) -> Iterator[None]:
        if _WINDOWS:
            security = _WindowsSecurity()
            handle = security.open(self.root, share_delete=True)
            try:
                security.verify(handle, directory=True)
                yield
            finally:
                security.close(handle)
        else:
            self._check(self.root)
            yield

    def _open(self, path: Path, *, create: bool, share_delete: bool = False) -> int:
        if _WINDOWS:
            import msvcrt

            security = _WindowsSecurity()
            handle = (
                security.create(path, directory=False, share_delete=share_delete)
                if create else security.open(path)
            )
            assert handle is not None
            try:
                security.verify(handle, directory=False)
                fd = int(
                    getattr(msvcrt, "open_osfhandle")(  # noqa: B009
                        handle,
                        (os.O_WRONLY if create else os.O_RDONLY) | getattr(os, "O_BINARY"),  # noqa: B009
                    )
                )
            except BaseException:
                security.close(handle)
                if create:
                    path.unlink(missing_ok=True)
                raise
            return fd
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL if create else os.O_RDONLY
        fd = os.open(path, flags | os.O_NOFOLLOW, 0o600)  # type: ignore[attr-defined, unused-ignore]
        try:
            self._check_info(os.fstat(fd), directory=False)
        except BaseException:
            os.close(fd)
            if create:
                path.unlink(missing_ok=True)
            raise
        return fd

    @staticmethod
    def _check_info(info: os.stat_result, *, directory: bool) -> None:
        if (
            info.st_uid != os.getuid()  # type: ignore[attr-defined, unused-ignore]
            or stat.S_IMODE(info.st_mode) & 0o077
            or (not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1))
            or (directory and not stat.S_ISDIR(info.st_mode))
        ):
            raise PermissionError("private state permissions are not restricted")

    def _check(self, path: Path) -> None:
        self._check_info(path.stat(follow_symlinks=False), directory=path == self.root)

    def write(self, identity: str, data: bytes) -> dict[str, str]:
        with self._directory():
            name = storage_id(identity) + ".bin"
            path = safe_path(self.root, name)
            fd = self._open(path, create=True)
            try:
                with os.fdopen(fd, "wb") as target:
                    target.write(data)
                    target.flush()
                    os.fsync(target.fileno())
                fsync_directory(self.root)
            except BaseException:
                path.unlink(missing_ok=True)
                raise
            return {"private_relpath": name, "sha256": hashlib.sha256(data).hexdigest()}

    def read_response(self, job_id: str, locator_json: str) -> dict[str, Any]:
        locator = json.loads(locator_json)
        if not isinstance(locator, dict) or set(locator) != {"private_relpath", "sha256"}:
            raise ValueError("invalid private response locator")
        if (
            locator["private_relpath"] != job_id + ".bin"
            or not isinstance(locator["sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", locator["sha256"]) is None
        ):
            raise ValueError("invalid private response locator")
        value = json.loads(self.read(job_id, locator["sha256"]))
        if not isinstance(value, dict):
            raise ValueError("private response must be an object")
        return value

    def write_credential(self, profile_id: str, value: str) -> str:
        """Store or replace an API key and return its private: reference."""
        name = credential_name(profile_id)
        self.write_secret(name, value.encode("utf-8"))
        return "private:" + name

    def write_secret(self, identity: str, data: bytes) -> None:
        """Atomically replace a secret without the create-only contract of write()."""
        with self._directory():
            name = storage_id(identity) + ".bin"
            path = safe_path(self.root, name)
            temporary = safe_path(self.root, name + ".tmp")
            temporary.unlink(missing_ok=True)
            fd = self._open(temporary, create=True, share_delete=True)
            try:
                with os.fdopen(fd, "wb") as target:
                    target.write(data)
                    target.flush()
                    os.fsync(target.fileno())
                # Windows scanners can briefly hold the just-written file.
                for delay in (0.05, 0.1, 0.2, 0.4, 0.8, 1.0, 1.0, 1.0, None):
                    try:
                        os.replace(temporary, path)
                        break
                    except PermissionError:
                        if not _WINDOWS or delay is None:
                            raise
                        time.sleep(delay)
                fsync_directory(self.root)
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise

    def read_secret(self, identity: str) -> str:
        with self._directory():
            path = safe_path(self.root, storage_id(identity) + ".bin")
            fd = self._open(path, create=False)
            with os.fdopen(fd, "rb") as source:
                data = source.read(MAX_SECRET_BYTES + 1)
        if len(data) > MAX_SECRET_BYTES:
            raise ValueError("credential is too large")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("credential is not UTF-8") from error

    def delete(self, relpath: str) -> None:
        with self._directory():
            safe_path(self.root, relpath).unlink(missing_ok=True)

    def identities(self) -> list[str]:
        with self._directory():
            return sorted(
                path.name.removesuffix(".bin")
                for path in self.root.iterdir()
                if path.name.endswith(".bin") and path.is_file()
            )

    def read(self, identity: str, expected_sha256: str) -> bytes:
        with self._directory():
            path = safe_path(self.root, storage_id(identity) + ".bin")
            fd = self._open(path, create=False)
            with os.fdopen(fd, "rb") as source:
                data = source.read()
        if hashlib.sha256(data).hexdigest() != expected_sha256:
            raise ValueError("private state hash mismatch")
        return data
