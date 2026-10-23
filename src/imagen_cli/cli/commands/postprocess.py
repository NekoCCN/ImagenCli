"""`imagen transform`: deterministic local pipelines over one artifact."""

from __future__ import annotations

from typing import Any, cast

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
        for item in rest.split(","):
            key, equals, raw = item.partition("=")
            if not equals or not key:
                raise ValueError("op parameters must be key=value")
            params[key] = _value(raw)
    return {"op": name, "params": params}


def transform_(
    repositories: Repositories,
    store: ArtifactStore,
    artifact: str,
    *,
    pipeline: str | None = None,
    op: str | None = None,
    session: str | None = None,
) -> tuple[str, dict[str, Any]]:
    steps: list[dict[str, Any]]
    if pipeline is not None:
        from imagen_cli.cli.input_readers import read_json

        steps = cast(list[dict[str, Any]], read_json(pipeline))
    elif op is not None:
        steps = [parse_op(op)]
    else:
        raise ValueError("pipeline or op is required")
    row = apply(repositories, store, artifact, steps, session_id=session)
    return "succeeded", {"artifact": row, "source": artifact}
