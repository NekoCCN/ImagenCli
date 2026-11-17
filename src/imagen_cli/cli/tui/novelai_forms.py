"""Offline NovelAI form conversion. Images use artifact selectors, never JSON paths."""

import json
import re
from typing import Any

from imagen_cli.providers.novelai_parameters import PARAMETER_KINDS, normalize_parameters


def parameters(values: dict[str, str], *, operation: str, model: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, kind in PARAMETER_KINDS.items():
        raw = values.get("nai_" + name, "")
        if not raw:
            continue
        try:
            result[name] = (
                json.loads(raw)
                if kind in {"integer", "number", "count", "boolean", "object"} else raw
            )
        except ValueError:
            # Identify the field, never echo user text or JSON exception details.
            raise ValueError(f"Invalid JSON in {name} / {name} JSON 格式错误") from None
    # Validate with exactly the production schema, preserving user-facing aliases.
    normalize_parameters(result, operation=operation, model=model)
    return result


def reference(values: dict[str, str]) -> tuple[list[str], list[dict[str, Any]]]:
    ordered = values.get("nai_references", "")
    aligned = values.get("nai_reference_options", "")
    if ordered or aligned:
        from pydantic import TypeAdapter

        from imagen_cli.domain.contracts import ReferenceOptions

        artifacts = json.loads(ordered or "[]")
        if (
            not isinstance(artifacts, list)
            or not 1 <= len(artifacts) <= 100
            or any(
                not isinstance(item, str) or re.fullmatch(r"art_[0-9a-f]{32}", item) is None
                for item in artifacts
            )
        ):
            raise ValueError("Ordered references require artifact IDs only / 有序参考仅接受产物 ID")
        options = TypeAdapter(list[ReferenceOptions]).validate_json(aligned or "[]")
        if len(artifacts) != len(options):
            raise ValueError(
                "Each artifact requires aligned role/settings / 每张图需对应角色和设置"
            )
        return ["artifact:" + item for item in artifacts], [
            option.model_dump(mode="json") for option in options
        ]
    artifact = values.get("nai_reference", "")
    if not artifact:
        return [], []
    kind = values.get("nai_role", "")
    if kind not in {"vibe", "precise"}:
        raise ValueError("Choose an explicit reference role / 请选择参考图角色")
    settings: dict[str, Any] = {}
    for name in ("strength", "information_extracted", "fidelity"):
        raw = values.get("nai_ref_" + name, "")
        if raw:
            settings[name] = float(raw)
    if kind == "precise":
        settings["mode"] = values.get("nai_ref_mode", "") or "character"
    elif "fidelity" in settings:
        raise ValueError("fidelity is precise-only / fidelity 仅用于精确参考")
    return ["artifact:" + artifact], [{"kind": kind, "settings": settings}]
