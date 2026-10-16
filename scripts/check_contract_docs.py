"""Offline contract check from the quickstart: JSON schemas, examples and links."""

from __future__ import annotations

import json
import re
import socket
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "specs" / "001-image-workbench" / "contracts"
DOC_DIRS = (ROOT / "specs" / "001-image-workbench", ROOT / "docs")
LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def main() -> int:
    # The local registry resolves every $ref; https://imagen-cli.invalid is only an id.
    socket.socket = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network"))
    socket.getaddrinfo = socket.socket  # type: ignore[assignment]

    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    schemas: dict[str, dict[str, Any]] = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in CONTRACTS.glob("*.schema.json")
    }
    registry = Registry().with_resources(
        [(schema["$id"], Resource.from_contents(schema)) for schema in schemas.values()]
    )

    def validator(name: str) -> Draft202012Validator:
        return Draft202012Validator(schemas[name], registry=registry)

    examples = json.loads((CONTRACTS / "examples.json").read_text(encoding="utf-8"))
    creation = validator("creation-input.schema.json")
    result = validator("result.schema.json")
    event = validator("event.schema.json")
    checks = 0
    for example in examples["creation_inputs"]:
        creation.validate(example)
        checks += 1
    for example in examples["invalid_creation_inputs"]:
        if creation.is_valid(example):
            raise ValueError(f"invalid creation example accepted: {example}")
        checks += 1
    for example in examples["results"]:
        result.validate(example)
        checks += 1
    for invocation in examples["jsonl_invocations"]:
        rows = invocation if isinstance(invocation, list) else [invocation]
        for row in rows:
            event.validate(row)
            if row["event"] == "terminal":
                result.validate(row["result"])
        checks += 1

    missing: list[dict[str, str]] = []
    for path in (item for directory in DOC_DIRS for item in directory.rglob("*.md")):
        for target in LINK.findall(path.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("#"):
                continue
            relative = target.split("#", 1)[0]
            if relative and not (path.parent / relative).exists():
                missing.append({"document": str(path.relative_to(ROOT)), "target": target})
    print(json.dumps({
        "json_files": len(list(CONTRACTS.glob("*.json"))),
        "schema_checks": checks,
        "missing_links": missing,
    }, ensure_ascii=False))
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
