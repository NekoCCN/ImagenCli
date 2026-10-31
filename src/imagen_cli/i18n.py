"""Interface language selection for human-facing CLI and TUI text.

Machine-readable keys and identifiers never change; only displayed text is
translated. English is the default; ``IMAGEN_LANG`` or ``--lang`` select
Chinese (``zh-CN``).
"""

from __future__ import annotations

import json
import os
import tempfile
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any

from platformdirs import user_config_path

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
_saved_loaded = False
_saved_value: str | None = None
CONFIG_FILE = "config.json"


def normalize(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return _ALIASES.get(value.strip().lower())


def config_path() -> Path:
    return user_config_path("imagen-cli") / CONFIG_FILE


def saved_language() -> str | None:
    global _saved_loaded, _saved_value
    if not _saved_loaded:
        try:
            payload = json.loads(config_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = None
        _saved_value = normalize(payload.get("language")) if isinstance(payload, dict) else None
        _saved_loaded = True
    return _saved_value


def save_language(value: str) -> str:
    global _saved_loaded, _saved_value
    normalized = normalize(value)
    if normalized is None:
        raise ValueError("unsupported language")
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload: dict[str, Any] = {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            payload = loaded
    except (OSError, ValueError):
        pass
    payload["language"] = normalized
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    _saved_value = normalized
    _saved_loaded = True
    return normalized


def language() -> str:
    selected = _selected.get()
    if selected is not None:
        return selected
    environment = normalize(os.environ.get("IMAGEN_LANG"))
    if environment is not None:
        return environment
    return saved_language() or DEFAULT


def set_language(value: str) -> Token[str | None]:
    normalized = normalize(value)
    if normalized is None:
        raise ValueError("unsupported language")
    return _selected.set(normalized)


def reset_language(token: Token[str | None] | None = None) -> None:
    global _saved_loaded
    _saved_loaded = False
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
