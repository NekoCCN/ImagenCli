"""Local filesystem trust-boundary helpers; no extraction or network side effects."""

import ctypes
import math
import os
import re
import stat
import unicodedata
from ctypes import wintypes
from pathlib import Path, PurePosixPath
from typing import Any
from zipfile import ZipFile

PRIVATE_FIELDS = frozenset(
    {
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "authorization",
        "password",
        "secret",
        "signature",
        "native_signature",
        "raw_response",
        "private_locator_ref",
        "private_relpath",
        "remote_id",
        "idempotency_key",
        "temporary_url",
        "signed_url",
    }
)


def relative_path(name: str) -> PurePosixPath:
    if (
        not isinstance(name, str)
        or not name
        or "\x00" in name
        or "\\" in name
        or ":" in name
        or name.startswith("/")
        or any(part in {"", ".", ".."} for part in name.rstrip("/").split("/"))
    ):
        raise ValueError("unsafe relative path")
    return PurePosixPath(name)


def safe_path(root: Path, name: str) -> Path:
    relative = relative_path(name)
    root = Path(root).absolute()
    if root.is_symlink():
        raise ValueError("symlink root")
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("symlink path")
        if path.exists() and path.is_file() and path.stat().st_nlink != 1:
            raise ValueError("hard-linked file")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("path escapes root")
    return path


def validate_manifest(value: object) -> None:
    ids: set[str] = set()

    def visit(item: object) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError("JSON object keys must be strings")
                normalized_key = re.sub(r"[^a-z0-9]", "", key.casefold())
                if normalized_key in {
                    re.sub(r"[^a-z0-9]", "", field) for field in PRIVATE_FIELDS
                } | {"downloadurl", "nativeparts"}:
                    raise ValueError("private field forbidden")
                if key == "id":
                    if not isinstance(child, str) or child in ids:
                        raise ValueError("invalid or duplicate ID")
                    ids.add(child)
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("non-finite JSON number")

    visit(value)


def validate_zip(
    archive: ZipFile,
    *,
    max_files: int = 10_000,
    max_file_size: int = 64 * 1024 * 1024,
    max_total_size: int = 1024 * 1024 * 1024,
) -> list[str]:
    infos = archive.infolist()
    if len(infos) > max_files:
        raise ValueError("too many ZIP entries")
    names: set[str] = set()
    files: list[str] = []
    total = 0
    for info in infos:
        relative_path(info.filename)
        normalized = unicodedata.normalize("NFC", info.filename.rstrip("/")).casefold()
        if normalized in names:
            raise ValueError("duplicate normalized ZIP path")
        names.add(normalized)
        mode = info.external_attr >> 16
        kind = stat.S_IFMT(mode)
        if info.flag_bits & 1 or kind not in {0, stat.S_IFREG, stat.S_IFDIR}:
            raise ValueError("encrypted or linked ZIP entry")
        if info.is_dir():
            continue
        if info.file_size > max_file_size:
            raise ValueError("ZIP file too large")
        total += info.file_size
        if total > max_total_size:
            raise ValueError("ZIP total too large")
        # Read bounded streams as well: do not trust declared sizes or CRCs alone.
        actual = 0
        with archive.open(info) as source:
            while chunk := source.read(min(64 * 1024, max_file_size - actual + 1)):
                actual += len(chunk)
                if actual > max_file_size:
                    raise ValueError("ZIP expanded file too large")
        if actual != info.file_size:
            raise ValueError("ZIP size mismatch")
        files.append(info.filename)
    # Reject file/directory collisions independently of archive ordering.
    file_names = {unicodedata.normalize("NFC", name).casefold() for name in files}
    for name in names:
        if any(
            str(parent) in file_names
            for parent in PurePosixPath(name).parents
            if str(parent) != "."
        ):
            raise ValueError("ZIP file/directory collision")
    return files


_WINDOWS = os.name == "nt"


def _validate_windows_acl(
    owner: str, user: str, control: int, entries: list[tuple[int, int, int, str]]
) -> None:
    # Only our exact protected, single-user ACL is accepted; unknown ACEs fail closed.
    if (
        owner != user
        or not control & 0x1000  # SE_DACL_PROTECTED
        or len(entries) != 1
        or entries[0] not in [(0, 0, 0x1F01FF, user), (0, 3, 0x1F01FF, user)]
    ):
        raise PermissionError("Windows ACL is not restricted to the current user")


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.DWORD),
        ("descriptor", wintypes.LPVOID),
        ("inherit", wintypes.BOOL),
    ]


class _WindowsSecurity:
    """Win32 handles and protected DACLs; no chmod or inherited-ACL fallback."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise PermissionError("Windows ACL APIs unavailable")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined, unused-ignore]
        self.advapi = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined, unused-ignore]
        signatures: list[tuple[Any, str, list[Any], Any]] = [
            (self.kernel, "GetCurrentProcess", [], wintypes.HANDLE),
            (self.kernel, "CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
            (self.kernel, "LocalFree", [wintypes.LPVOID], wintypes.LPVOID),
            (self.kernel, "CreateDirectoryW", [wintypes.LPCWSTR, wintypes.LPVOID], wintypes.BOOL),
            (
                self.kernel,
                "CreateFileW",
                [
                    wintypes.LPCWSTR,
                    wintypes.DWORD,
                    wintypes.DWORD,
                    wintypes.LPVOID,
                    wintypes.DWORD,
                    wintypes.DWORD,
                    wintypes.HANDLE,
                ],
                wintypes.HANDLE,
            ),
            (
                self.kernel,
                "GetFileInformationByHandle",
                [wintypes.HANDLE, wintypes.LPVOID],
                wintypes.BOOL,
            ),
            (
                self.advapi,
                "OpenProcessToken",
                [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID],
                wintypes.BOOL,
            ),
            (
                self.advapi,
                "GetTokenInformation",
                [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID],
                wintypes.BOOL,
            ),
            (
                self.advapi,
                "ConvertSidToStringSidW",
                [wintypes.LPVOID, wintypes.LPVOID],
                wintypes.BOOL,
            ),
            (
                self.advapi,
                "SetFileSecurityW",
                [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID],
                wintypes.BOOL,
            ),
            (
                self.advapi,
                "ConvertStringSecurityDescriptorToSecurityDescriptorW",
                [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID],
                wintypes.BOOL,
            ),
            (
                self.advapi,
                "GetSecurityInfo",
                [
                    wintypes.HANDLE,
                    ctypes.c_int,
                    wintypes.DWORD,
                    wintypes.LPVOID,
                    wintypes.LPVOID,
                    wintypes.LPVOID,
                    wintypes.LPVOID,
                    wintypes.LPVOID,
                ],
                wintypes.DWORD,
            ),
            (
                self.advapi,
                "GetSecurityDescriptorControl",
                [wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID],
                wintypes.BOOL,
            ),
            (
                self.advapi,
                "GetAce",
                [wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID],
                wintypes.BOOL,
            ),
        ]
        for library, name, args, result in signatures:
            function = getattr(library, name)
            function.argtypes = args
            function.restype = result
        token = wintypes.HANDLE()
        self._ok(
            self.advapi.OpenProcessToken(self.kernel.GetCurrentProcess(), 8, ctypes.byref(token))
        )
        try:
            size = wintypes.DWORD()
            self.advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
            buffer = ctypes.create_string_buffer(size.value)
            self._ok(self.advapi.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)))
            self.user = self._sid(ctypes.cast(buffer, ctypes.POINTER(wintypes.LPVOID))[0])
        finally:
            self.kernel.CloseHandle(token)

    def _ok(self, result: Any) -> None:
        if not result:
            raise PermissionError(
                f"Windows ACL operation failed ({getattr(ctypes, 'get_last_error')()})"  # noqa: B009
            )

    def _sid(self, sid: Any) -> str:
        text = wintypes.LPWSTR()
        self._ok(self.advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)))
        try:
            return str(text.value)
        finally:
            self.kernel.LocalFree(text)

    def create(self, path: Path, *, directory: bool) -> int | None:
        descriptor = wintypes.LPVOID()
        flags = "OICI" if directory else ""
        sddl = f"O:{self.user}D:P(A;{flags};FA;;;{self.user})"
        self._ok(
            self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl, 1, ctypes.byref(descriptor), None
            )
        )
        try:
            attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
            if directory:
                self._ok(self.kernel.CreateDirectoryW(str(path), ctypes.byref(attributes)))
                return None
            handle = self.kernel.CreateFileW(
                str(path), 0xC0020000, 0, ctypes.byref(attributes), 1, 0x00200000, None
            )
            if handle == ctypes.c_void_p(-1).value:
                error = getattr(ctypes, "get_last_error")()  # noqa: B009
                if error in {80, 183}:
                    raise FileExistsError(str(path))
                self._ok(False)
            return int(handle)
        finally:
            self.kernel.LocalFree(descriptor)

    def tighten(self, path: Path) -> None:
        """Replace an inherited DACL on a pre-existing directory with the protected one."""
        descriptor = wintypes.LPVOID()
        sddl = f"O:{self.user}D:P(A;OICI;FA;;;{self.user})"
        self._ok(
            self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl, 1, ctypes.byref(descriptor), None
            )
        )
        try:
            # DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION
            self._ok(self.advapi.SetFileSecurityW(str(path), 0x80000004, descriptor))
        finally:
            self.kernel.LocalFree(descriptor)

    def open(self, path: Path) -> int:
        handle = self.kernel.CreateFileW(
            str(path), 0x80020000, 1, None, 3, 0x02200000, None
        )  # OPEN_REPARSE_POINT | BACKUP_SEMANTICS; no write/delete sharing.
        if handle == ctypes.c_void_p(-1).value:
            self._ok(False)
        return int(handle)

    def verify(self, handle: int, *, directory: bool) -> None:
        # BY_HANDLE_FILE_INFORMATION is 13 DWORDs; attributes and link count are fixed fields.
        info = (wintypes.DWORD * 13)()
        self._ok(self.kernel.GetFileInformationByHandle(handle, info))
        if (
            info[0] & 0x400
            or bool(info[0] & 0x10) != directory
            or (not directory and info[10] != 1)
        ):
            raise PermissionError("reparse point, hard link, or unexpected private object type")
        owner, dacl, descriptor = wintypes.LPVOID(), wintypes.LPVOID(), wintypes.LPVOID()
        error = self.advapi.GetSecurityInfo(
            handle,
            1,
            5,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
        if error:
            raise PermissionError(f"Windows ACL query failed ({error})")
        try:
            control, revision = wintypes.WORD(), wintypes.DWORD()
            self._ok(
                self.advapi.GetSecurityDescriptorControl(
                    descriptor, ctypes.byref(control), ctypes.byref(revision)
                )
            )
            entries: list[tuple[int, int, int, str]] = []
            if dacl.value:
                count = ctypes.cast(dacl, ctypes.POINTER(wintypes.WORD))[2]
                for index in range(count):
                    ace = wintypes.LPVOID()
                    self._ok(self.advapi.GetAce(dacl, index, ctypes.byref(ace)))
                    header = ctypes.cast(ace, ctypes.POINTER(ctypes.c_ubyte))
                    if header[0] != 0:
                        raise PermissionError("unsupported Windows ACE")
                    mask = ctypes.cast(ace, ctypes.POINTER(wintypes.DWORD))[1]
                    if ace.value is None:
                        raise PermissionError("missing Windows ACE")
                    entries.append((header[0], header[1], mask, self._sid(ace.value + 8)))
            _validate_windows_acl(self._sid(owner), self.user, control.value, entries)
        finally:
            self.kernel.LocalFree(descriptor)

    def close(self, handle: int) -> None:
        self.kernel.CloseHandle(handle)


def _reject_link(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("symlink or reparse directory")


def secure_directory(path: Path) -> Path:
    path = Path(path).absolute()
    for parent in (path, *path.parents):
        _reject_link(parent)
    if _WINDOWS:
        security = _WindowsSecurity()
        missing = []
        parent = path
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        if not missing:
            # A pre-existing workspace keeps its inherited ACL until we replace it.
            security.tighten(path)
        for parent in reversed(missing):
            security.create(parent, directory=True)
        handle = security.open(path)
        try:
            security.verify(handle, directory=True)
        finally:
            security.close(handle)
    elif os.name == "posix":
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.stat().st_uid != os.getuid():  # type: ignore[attr-defined, unused-ignore]
            raise PermissionError("directory not owned by current user")
        path.chmod(0o700)
    else:
        raise PermissionError("current-user permissions unavailable")
    return path


def fsync_directory(path: Path) -> None:
    if os.name == "posix":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)  # type: ignore[attr-defined, unused-ignore]
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def storage_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,127}", value):
        raise ValueError("invalid storage ID")
    return value
