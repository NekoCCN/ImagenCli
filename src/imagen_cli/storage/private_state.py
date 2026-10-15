"""Opaque private state: fail closed without verified current-user permissions."""

import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from imagen_cli.storage.safety import (
    _WINDOWS,
    _WindowsSecurity,
    fsync_directory,
    safe_path,
    secure_directory,
    storage_id,
)


class PrivateStateStore:
    def __init__(self, root: Path):
        self.root = secure_directory(root)

    @contextmanager
    def _directory(self) -> Iterator[None]:
        if _WINDOWS:
            security = _WindowsSecurity()
            handle = security.open(self.root)
            try:
                security.verify(handle, directory=True)
                yield
            finally:
                security.close(handle)
        else:
            self._check(self.root)
            yield

    def _open(self, path: Path, *, create: bool) -> int:
        if _WINDOWS:
            import msvcrt

            security = _WindowsSecurity()
            handle = security.create(path, directory=False) if create else security.open(path)
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

    def read(self, identity: str, expected_sha256: str) -> bytes:
        with self._directory():
            path = safe_path(self.root, storage_id(identity) + ".bin")
            fd = self._open(path, create=False)
            with os.fdopen(fd, "rb") as source:
                data = source.read()
        if hashlib.sha256(data).hexdigest() != expected_sha256:
            raise ValueError("private state hash mismatch")
        return data
