"""User-configurable local limits for post-processing.

Environment variables win over ``config.json`` (the file that also stores the
interface language):

- ``IMAGEN_POSTPROCESS_TIMEOUT``: seconds; ``0``/``off``/``none``/``unlimited``
  removes the deadline, unset keeps the adaptive default.
- ``IMAGEN_POSTPROCESS_MEMORY_MB``: mebibytes; ``0``/``off``/``none``/``unlimited``
  removes the working-memory cap and its confirmation prompt, unset keeps 2 GiB.

The same values may be written in ``config.json`` as
``postprocess_timeout_seconds`` and ``postprocess_memory_mb``.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any

from imagen_cli import i18n

DEFAULT_MEMORY_BYTES = 2 * 1024**3
_UNLIMITED = {"0", "off", "none", "unlimited"}


def _config() -> dict[str, Any]:
    try:
        payload = json.loads(i18n.config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _raw(name: str, key: str) -> Any:
    value = os.environ.get(name)
    return _config().get(key) if value is None else value


def _seconds(name: str, key: str) -> float | None:
    value = _raw(name, key)
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in _UNLIMITED:
        return 0.0
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number of seconds or 'unlimited'")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number of seconds or 'unlimited'") from None
    if number < 0 or not math.isfinite(number):
        raise ValueError(f"{name} must be a non-negative number of seconds")
    return number


def _megabytes(name: str, key: str) -> int | None:
    value = _raw(name, key)
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in _UNLIMITED:
        return 0
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a whole number of MiB or 'unlimited'")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a whole number of MiB or 'unlimited'") from None
    if number < 0:
        raise ValueError(f"{name} must be a non-negative number of MiB")
    return number


def timeout_limit() -> float | None:
    """Fixed deadline in seconds; 0 means no deadline, None means adaptive."""
    return _seconds("IMAGEN_POSTPROCESS_TIMEOUT", "postprocess_timeout_seconds")


def memory_limit() -> int | None:
    """Working-memory cap in bytes; 0 means unlimited, None means the default."""
    megabytes = _megabytes("IMAGEN_POSTPROCESS_MEMORY_MB", "postprocess_memory_mb")
    return None if megabytes is None else megabytes * 1024**2
