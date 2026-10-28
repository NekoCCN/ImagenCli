"""`imagen transform`: deterministic local pipelines over one artifact."""

from __future__ import annotations

import difflib
import json
import re
from typing import Any, cast

from imagen_cli import i18n
from imagen_cli.application.postprocess_service import apply
from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.repositories import Repositories


def _value(text: str) -> Any:
    if text in {"true", "false"}:
        return text == "true"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def parse_op(specification: str) -> dict[str, Any]:
    name, separator, rest = specification.partition(":")
    params: dict[str, Any] = {}
    if separator:
        remaining = rest
        while remaining:
            match = re.match(r"([A-Za-z_][A-Za-z_0-9]*)=", remaining)
            if match is None:
                raise ValueError("op parameters must be key=value")
            key = match[1]
            if key in params:
                raise ValueError(f"duplicate op parameter: {key}")
            remaining = remaining[match.end():]
            if remaining.startswith('"'):
                try:
                    value, end = json.JSONDecoder().raw_decode(remaining)
                except ValueError as error:
                    raise ValueError("invalid quoted op parameter") from error
                remaining = remaining[end:]
                if remaining and not remaining.startswith(","):
                    raise ValueError("expected comma after quoted op parameter")
            else:
                boundary = re.search(r",(?=[A-Za-z_][A-Za-z_0-9]*=)", remaining)
                end = boundary.start() if boundary else len(remaining)
                raw, remaining = remaining[:end], remaining[end:]
                if "," in raw:
                    if key not in {"frequencies", "color", "quad"}:
                        raise ValueError("quote op values containing commas")
                    try:
                        for part in raw.replace(";", ",").split(","):
                            float(part)
                    except ValueError as error:
                        raise ValueError("invalid coordinate or color value") from error
                value = _value(raw)
            params[key] = value
            if remaining:
                remaining = remaining[1:]
                if not remaining:
                    raise ValueError("trailing comma in op parameters")
        if not rest:
            raise ValueError("op parameters must be key=value")
    return {"op": name, "params": params}


def transform_(
    repositories: Repositories,
    store: ArtifactStore,
    artifact: str,
    *,
    pipeline: str | None = None,
    op: str | None = None,
    preset: str | None = None,
    session: str | None = None,
    timeout: float = 120,
    budget_approval: str | None = None,
    model_params: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    steps: list[dict[str, Any]]
    if model_params is not None:
        steps = [{"op": "superres", "params": model_params}]
    elif pipeline is not None:
        from imagen_cli.cli.input_readers import read_json

        payload = read_json(pipeline, allow_array=True)
        if isinstance(payload, dict):
            from imagen_cli.application.postprocess_service import replay_pipeline

            steps = replay_pipeline(payload)
        else:
            steps = cast(list[dict[str, Any]], payload)
    elif op is not None:
        steps = [parse_op(op)]
    elif preset is not None:
        from imagen_cli.application.operator_catalog import PRESET_PIPELINES

        match = None
        if preset in PRESET_PIPELINES:
            match = PRESET_PIPELINES[preset]
        else:
            for k, v in PRESET_PIPELINES.items():
                if preset.lower() in k.lower():
                    match = v
                    break
        if match is None:
            matches = difflib.get_close_matches(preset, list(PRESET_PIPELINES), n=3)
            suggestion = ""
            if matches:
                suggestion = i18n.t(
                    f" Did you mean {', '.join(matches)}?", f"是不是 {'、'.join(matches)}？"
                )
            raise ValueError(
                i18n.t(
                    f"Unknown preset '{preset}'.{suggestion} Run `imagen ops` to list them.",
                    f"未知预设 '{preset}'。{suggestion}运行 `imagen ops` 查看列表。",
                )
            )
        steps = match
    else:
        raise ValueError("pipeline, op or preset is required")
    from imagen_cli.application.postprocess_service import BudgetApprovalRequired

    try:
        row = apply(repositories, store, artifact, steps, session_id=session, timeout=timeout,
                    budget_approval=budget_approval)
    except BudgetApprovalRequired as error:
        return "approval_required", {"budget": error.report,
                                    "error": {"type": "approval_required", "detail": str(error)}}
    return "succeeded", {"artifact": row, "source": artifact}
