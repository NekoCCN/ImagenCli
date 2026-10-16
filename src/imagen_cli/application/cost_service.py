"""Normalize public usage without inventing billing amounts."""

from collections.abc import Mapping
from typing import Any

from imagen_cli.domain.models import Cost


class CostService:
    _USAGE_KEYS = {
        "input_tokens", "output_tokens", "total_tokens", "promptTokenCount",
        "candidatesTokenCount", "totalTokenCount", "images",
    }

    @classmethod
    def normalize(cls, usage: Any) -> Cost:
        safe: dict[str, int] = {}
        if isinstance(usage, Mapping):
            for key, value in usage.items():
                if key in cls._USAGE_KEYS and type(value) is int and value >= 0:
                    safe[key] = value
        return Cost.model_validate({
            "estimate": None, "actual": None, "currency": None, "usage": safe or None,
            "unknown_reason": "provider did not supply a verifiable billing amount",
        })
