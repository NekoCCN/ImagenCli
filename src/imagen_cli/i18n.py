"""Interface language selection for human-facing CLI and TUI text.

Machine-readable keys and identifiers never change; only displayed text is
translated. English is the default; ``IMAGEN_LANG`` or ``--lang`` select
Chinese (``zh-CN``).
"""

from __future__ import annotations

import os
from contextvars import ContextVar, Token
from typing import Any

DEFAULT = "en"
_ALIASES = {
    "en": "en",
    "en-us": "en",
    "en-gb": "en",
    "zh": "zh-CN",
    "zh-cn": "zh-CN",
    "zh-hans": "zh-CN",
    "zh-sg": "zh-CN",
}
_selected: ContextVar[str | None] = ContextVar("imagen_language", default=None)


def normalize(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return _ALIASES.get(value.strip().lower())


def language() -> str:
    selected = _selected.get()
    if selected is not None:
        return selected
    return normalize(os.environ.get("IMAGEN_LANG")) or DEFAULT


def set_language(value: str) -> Token[str | None]:
    normalized = normalize(value)
    if normalized is None:
        raise ValueError("unsupported language")
    return _selected.set(normalized)


def reset_language(token: Token[str | None] | None = None) -> None:
    if token is None:
        _selected.set(None)
    else:
        _selected.reset(token)


def is_chinese() -> bool:
    return language() == "zh-CN"


def t(english: str, chinese: str) -> str:
    """Pick the display string for the active language."""
    return chinese if is_chinese() else english


def take_argument(argv: list[str]) -> tuple[list[str], str | None]:
    """Remove one ``--lang VALUE`` / ``--lang=VALUE`` and return its value."""
    remaining: list[str] = []
    chosen: str | None = None
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in {"--zh", "--en"}:
            value = "zh-CN" if token == "--zh" else "en"
            if chosen is not None:
                raise ValueError("duplicate option")
            chosen = value
            index += 1
            continue
        if token == "--lang":
            index += 1
            if index == len(argv) or argv[index].startswith("-"):
                raise ValueError("option needs value")
            value = argv[index]
        elif token.startswith("--lang="):
            value = token.partition("=")[2]
        else:
            remaining.append(token)
            index += 1
            continue
        if not value or "\0" in value:
            raise ValueError("option needs text")
        if chosen is not None:
            raise ValueError("duplicate option")
        chosen = value
        index += 1
    return remaining, chosen
