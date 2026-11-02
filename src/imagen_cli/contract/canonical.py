"""Restricted JCS-compatible canonical JSON; floats are refused by contract."""

from __future__ import annotations

import json
from typing import Any


def _restrict(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, list):
        return [_restrict(item) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical JSON keys must be strings")
            result[key] = _restrict(item)
        return result
    raise TypeError(f"canonical JSON does not accept {type(value).__name__}")


def canonical(value: Any) -> bytes:
    """UTF-8, sorted keys, no whitespace, only bool/null/int/str/list/dict."""
    return json.dumps(
        _restrict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
