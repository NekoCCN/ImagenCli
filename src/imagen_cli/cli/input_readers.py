"""Bounded byte readers. No stdin access without an explicit caller request."""

import json
import math
import os
import selectors
import sys
import time
from pathlib import Path
from typing import Any, BinaryIO

TEXT_LIMIT = 1024 * 1024
JSON_LIMIT = 4 * TEXT_LIMIT
STDIN_TIMEOUT = 30


def read_stdin(
    stream: BinaryIO | None = None, *, limit: int = TEXT_LIMIT, timeout: float = STDIN_TIMEOUT
) -> bytes:
    stream = stream if stream is not None else sys.stdin.buffer
    deadline = time.monotonic() + timeout
    chunks = bytearray()
    if sys.platform == "win32":
        # Windows has no select() for pipes. Poll PeekNamedPipe for pipes and
        # kbhit for the console; regular files read straight through.
        import ctypes
        import msvcrt

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined, unused-ignore]
        handle = ctypes.c_void_p(msvcrt.get_osfhandle(stream.fileno()))
        kind = kernel32.GetFileType(handle)
        available = ctypes.c_ulong()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("stdin timeout")
            if kind == 3:  # pipe
                if not kernel32.PeekNamedPipe(
                    handle, None, 0, None, ctypes.byref(available), None
                ):
                    return bytes(chunks)
                if not available.value:
                    time.sleep(min(0.01, remaining))
                    continue
                size = min(available.value, 65536, limit + 1 - len(chunks))
            elif kind == 2:  # console
                if not msvcrt.kbhit():
                    time.sleep(min(0.01, remaining))
                    continue
                size = min(65536, limit + 1 - len(chunks))
            else:  # regular file
                size = min(65536, limit + 1 - len(chunks))
            chunk = os.read(stream.fileno(), size)
            if not chunk:
                return bytes(chunks)
            chunks.extend(chunk)
            if len(chunks) > limit:
                raise ValueError("input byte limit exceeded")

    # SelectSelector also accepts regular-file stdin redirected by the shell.
    with selectors.SelectSelector() as selector:
        selector.register(stream, selectors.EVENT_READ)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise ValueError("stdin timeout")
            chunk = os.read(stream.fileno(), min(65536, limit + 1 - len(chunks)))
            if not chunk:
                return bytes(chunks)
            chunks.extend(chunk)
            if len(chunks) > limit:
                raise ValueError("input byte limit exceeded")


def _decode(raw: bytes) -> str:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError:
        raise ValueError("invalid UTF-8") from None
    if "\0" in text:
        raise ValueError("NUL is forbidden")
    return text.replace("\r\n", "\n")


def _bytes(source: Any, limit: int) -> bytes:
    if source == "-":
        return read_stdin(limit=limit)
    if isinstance(source, (str, Path)):
        try:
            with open(source, "rb") as stream:
                return _bytes(stream, limit)
        except OSError:
            raise ValueError("input file unavailable") from None
    raw = source.read(limit + 1)
    if not isinstance(raw, bytes) or len(raw) > limit:
        raise ValueError("input byte limit exceeded")
    return bytes(raw)


def read_text(source: Any) -> str:
    text = _decode(_bytes(source, TEXT_LIMIT))
    if not text.strip():
        raise ValueError("prompt must not be blank")
    return text


def read_prompt(
    *, prompt_file: str | Path | None = None, prompt_stdin: bool = False, prompt: str | None = None
) -> str:
    if sum((prompt_file is not None, bool(prompt_stdin), prompt is not None)) != 1:
        raise ValueError("exactly one prompt source is required")
    if prompt is not None:
        if not isinstance(prompt, str):
            raise ValueError("prompt must be text")
        import io

        return read_text(io.BytesIO(prompt.encode("utf-8")))
    return read_text("-" if prompt_stdin else prompt_file)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _check(value: Any) -> None:
    if isinstance(value, str) and "\0" in value:
        raise ValueError("NUL is forbidden")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite JSON number")
    if isinstance(value, dict):
        for key, item in value.items():
            _check(key)
            _check(item)
    if isinstance(value, list):
        for item in value:
            _check(item)


def read_json(source: Any, *, allow_array: bool = False) -> Any:
    try:
        value = json.loads(_decode(_bytes(source, JSON_LIMIT)), object_pairs_hook=_pairs)
        _check(value)
    except (json.JSONDecodeError, RecursionError):
        raise ValueError("invalid JSON") from None
    if not isinstance(value, dict) and not (allow_array and isinstance(value, list)):
        raise ValueError("JSON object required")
    return value
