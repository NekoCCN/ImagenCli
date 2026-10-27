"""Explicit Windows elevation for permission failures; agents never self-elevate."""

from __future__ import annotations

import errno
import os
import shlex
import subprocess
import sys

from imagen_cli import i18n

_WINDOWS_SYMLINK_PRIVILEGE = 1314
_WINDOWS_ACCESS_DENIED = 5


def supported() -> bool:
    """Elevation can be requested only where the platform offers a consent prompt."""
    return sys.platform == "win32"


def is_permission_denied(error: BaseException) -> bool:
    if isinstance(error, PermissionError):
        return True
    if getattr(error, "winerror", None) in {
        _WINDOWS_ACCESS_DENIED,
        _WINDOWS_SYMLINK_PRIVILEGE,
    }:
        return True
    return isinstance(error, OSError) and error.errno in {errno.EACCES, errno.EPERM}


def message(error: BaseException) -> str:
    if getattr(error, "winerror", None) == _WINDOWS_SYMLINK_PRIVILEGE:
        return i18n.t(
            "Creating a symbolic link needs administrator rights or Windows Developer Mode.",
            "创建符号链接需要管理员权限或 Windows 开发者模式。",
        )
    return i18n.t(
        "Permission denied. Run the command with administrator rights "
        "(Windows shows a UAC prompt), or make the target writable.",
        "没有执行该操作的权限。请以管理员身份运行（Windows 会弹出 UAC 提示），"
        "或让目标位置可写。",
    )


def command_line(args: list[str]) -> str:
    """The same invocation a human would rerun; quoting follows the host shell."""
    quoted = (
        subprocess.list2cmdline(args) if sys.platform == "win32" else shlex.join(args)
    )
    return f"imagen {quoted}".rstrip()


def relaunch(args: list[str]) -> int | None:
    """Rerun this CLI elevated after a UAC prompt; None when it never started.

    The child opens its own console, so its output does not return here; only
    the exit code does. The working directory is fixed so relative arguments
    keep their meaning.
    """
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    see_mask_noclose_process = 0x00000040
    infinite = 0xFFFFFFFF
    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = see_mask_noclose_process
    info.lpVerb = "runas"
    info.lpFile = sys.executable
    info.lpParameters = subprocess.list2cmdline(["-m", "imagen_cli", *args])
    info.lpDirectory = os.getcwd()
    info.nShow = 1
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):  # type: ignore[attr-defined, unused-ignore]
        return None
    if not info.hProcess:
        return None
    try:
        ctypes.windll.kernel32.WaitForSingleObject(  # type: ignore[attr-defined, unused-ignore]
            info.hProcess, infinite
        )
        code = wintypes.DWORD()
        if not ctypes.windll.kernel32.GetExitCodeProcess(  # type: ignore[attr-defined, unused-ignore]
            info.hProcess, ctypes.byref(code)
        ):
            return None
        return int(code.value)
    finally:
        ctypes.windll.kernel32.CloseHandle(info.hProcess)  # type: ignore[attr-defined, unused-ignore]
