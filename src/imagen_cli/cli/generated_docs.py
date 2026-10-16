"""Generate help, schema examples and agent reference from the CLI registry.

Every table and example here is derived from ``registry.discover()``. Nothing in
this module maintains a second parameter list: if the registry changes, the
generated reference and the agent-runbook block drift until regenerated.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import jsonschema

from imagen_cli.capabilities import discovery as capability_discovery
from imagen_cli.capabilities.catalog_data import CANONICAL_RECORDS
from imagen_cli.cli.registry import COMMANDS, discover

BEGIN_MARKER = "<!-- BEGIN GENERATED: commands -->"
END_MARKER = "<!-- END GENERATED: commands -->"
AGENT_RUNBOOK = Path(__file__).resolve().parents[3] / "docs" / "agent-runbook.md"

_PATTERN_SAMPLES = {
    r"\S": "example",
    r"^job_[0-9a-f]{32}$": "job_" + "0" * 32,
    r"^bat_[0-9a-f]{32}$": "bat_" + "0" * 32,
    r"^pln_[0-9a-f]{32}$": "pln_" + "0" * 32,
    r"^art_[0-9a-f]{32}$": "art_" + "0" * 32,
    r"^[0-9a-f]{64}$": "0" * 64,
    r"^[A-Za-z0-9_-]{1,2048}$": "example",
    r"^(artifact:art_[0-9a-f]{32}|file:.+)$": "artifact:art_" + "0" * 32,
}
_CONSTRAINTS = (
    "const", "enum", "pattern", "minLength", "maxLength",
    "minimum", "maximum", "minItems", "maxItems",
)


def _escape(value: str) -> str:
    return value.replace("|", "\\|")


def _resolve(spec: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    pointer = spec.get("$ref")
    if not isinstance(pointer, str):
        return spec
    target: Any = root
    for part in pointer.removeprefix("#/").split("/"):
        target = target[part]
    return target if isinstance(target, dict) else {}


def _string_value(spec: dict[str, Any]) -> str:
    pattern = spec.get("pattern")
    value = _PATTERN_SAMPLES.get(pattern, "example") if isinstance(pattern, str) else "example"
    minimum = spec.get("minLength")
    if type(minimum) is int and len(value) < minimum:
        value += "x" * (minimum - len(value))
    maximum = spec.get("maxLength")
    if type(maximum) is int:
        value = value[:maximum]
    return value


def _apply_conditionals(example: dict[str, Any], schema: dict[str, Any]) -> None:
    for clause in schema.get("allOf", []):
        condition = clause.get("if", {})
        matches = all(
            example.get(key) == spec["const"]
            for key, spec in (condition.get("properties") or {}).items()
            if "const" in spec
        ) and all(key in example for key in condition.get("required", []))
        if not matches:
            continue
        consequence = clause.get("then", {})
        for key, spec in (consequence.get("properties") or {}).items():
            if key not in example:
                base = (schema.get("properties") or {}).get(key, {})
                example[key] = _sample({**base, **spec}, schema)
        for key in consequence.get("required", []):
            if key not in example:
                spec = {**(schema.get("properties") or {}).get(key, {}),
                        **((consequence.get("properties") or {}).get(key, {}))}
                example[key] = _sample(spec, schema)


def _sample(spec: dict[str, Any], root: dict[str, Any]) -> Any:
    spec = _resolve(spec, root)
    if "const" in spec:
        return spec["const"]
    if "enum" in spec:
        return spec["enum"][0]
    if "default" in spec:
        return spec["default"]
    kind = spec.get("type")
    if isinstance(kind, list):
        kind = next((item for item in kind if item != "null"), None)
    if kind == "integer":
        return spec.get("minimum", 0)
    if kind == "number":
        return spec.get("minimum", 0.0)
    if kind == "boolean":
        return False
    if kind == "string":
        return _string_value(spec)
    if kind == "array":
        count = spec.get("minItems", 0)
        return [_sample(spec.get("items", {}), root) for _ in range(count)]
    if kind == "object":
        properties = spec.get("properties") or {}
        example = {
            key: _sample(properties[key], root) for key in spec.get("required", [])
        }
        _apply_conditionals(example, spec)
        return example
    if kind == "null":
        return None
    return {}


def example_input(name: str) -> dict[str, Any]:
    """A minimal value that validates against the command's advertised input schema."""
    schema = discover(name)["input_schema"]
    value = _sample(schema, schema)
    if not isinstance(value, dict):
        raise ValueError("command input examples must be objects")
    return value


def capability_example() -> dict[str, Any]:
    """One real discovery result, shaped by the capabilities data schema."""
    report = capability_discovery.discover([CANONICAL_RECORDS[0]])
    value = {
        "items": list(report.items),
        "next_cursor": None,
        "evidence": report.evidence,
    }
    schema = discover("capabilities")["data_schema"]
    if not jsonschema.Draft202012Validator(schema).is_valid(value):
        raise ValueError("capability example does not match the registry data schema")
    return value


def _type_label(spec: dict[str, Any]) -> str:
    if "const" in spec:
        return "const"
    if "enum" in spec:
        return "enum"
    kind = spec.get("type")
    if isinstance(kind, list):
        return "|".join(str(item) for item in kind)
    return str(kind or "object")


def _parameter_rows(name: str) -> list[str]:
    schema = discover(name)["input_schema"]
    required = set(schema.get("required", []))
    rows: list[str] = []
    for parameter, raw in (schema.get("properties") or {}).items():
        spec = raw if isinstance(raw, dict) else {}
        constraints = "; ".join(
            f"{key}={json.dumps(spec[key], ensure_ascii=False)}"
            for key in _CONSTRAINTS if key in spec
        )
        default = json.dumps(spec["default"], ensure_ascii=False) if "default" in spec else ""
        rows.append(
            "| {name} | {kind} | {required} | {default} | {constraints} |".format(
                name=_escape(parameter),
                kind=_escape(_type_label(spec)),
                required="yes" if parameter in required else "no",
                default=_escape(default),
                constraints=_escape(constraints),
            )
        )
    return rows


def command_help(name: str) -> str:
    command = COMMANDS[name]
    schema = discover(name)["input_schema"]
    required = set(schema.get("required", []))
    lines = [
        f"{name} — {command.summary}",
        f"milestone: {command.milestone}; available: {'yes' if command.available else 'no'}",
        "parameters:",
    ]
    properties = schema.get("properties") or {}
    if not properties:
        lines.append("  (none)")
    for parameter, raw in properties.items():
        spec = raw if isinstance(raw, dict) else {}
        marker = "required" if parameter in required else "optional"
        default = f", default={json.dumps(spec['default'], ensure_ascii=False)}" if (
            "default" in spec
        ) else ""
        lines.append(f"  {parameter}: {_type_label(spec)} ({marker}{default})")
    return "\n".join(lines)


def command_index() -> list[str]:
    rows = [
        "| 命令 | 可用 | 里程碑 | 摘要 |",
        "| --- | --- | --- | --- |",
    ]
    for name, command in COMMANDS.items():
        rows.append(
            f"| `{_escape(name)}` | {'yes' if command.available else 'no'} "
            f"| {command.milestone} | {_escape(command.summary)} |"
        )
    return rows


def _json_block(value: Any) -> str:
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n```"


def render_markdown() -> str:
    lines = [
        "# Generated command reference",
        "",
        "Generated from `src/imagen_cli/cli/registry.py`; do not edit by hand.",
        "",
        "## Help index",
        "",
        *command_index(),
        "",
        "## Parameter tables",
        "",
        "| 命令 | 参数 | 类型 | 必填 | 默认 | 约束 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for name in COMMANDS:
        for row in _parameter_rows(name):
            lines.append(f"| `{name}` {row}")
    lines += ["", "## Input schema examples", ""]
    for name in COMMANDS:
        lines += [f"### `{name}`", "", _json_block(example_input(name)), ""]
    lines += [
        "## Capability output example",
        "",
        _json_block(capability_example()),
        "",
    ]
    return "\n".join(lines)


def agent_reference() -> str:
    lines = [
        "## 命令与参数（自动生成，勿手改）",
        "",
        "来源：`src/imagen_cli/cli/registry.py`。重新生成："
        "`uv run python -m imagen_cli.cli.generated_docs --write`。",
        "",
        *command_index(),
        "",
        "| 命令 | 参数 | 类型 | 必填 | 默认 | 约束 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for name in COMMANDS:
        for row in _parameter_rows(name):
            lines.append(f"| `{name}` {row}")
    lines += [
        "",
        "输入示例（最小合法值）：",
        "",
        _json_block({name: example_input(name) for name in COMMANDS}),
        "",
        "能力查询输出示例：",
        "",
        _json_block(capability_example()),
        "",
    ]
    return "\n".join(lines)


def check_markdown(text: str) -> list[str]:
    errors: list[str] = []
    for name in COMMANDS:
        heading = f"### `{name}`"
        if text.count(heading) != 1:
            errors.append(f"command heading appears {text.count(heading)} times: {heading}")
        for row in _parameter_rows(name):
            if f"| `{name}` {row}" not in text:
                errors.append(f"missing or stale parameter row for {name}: {row}")
    return errors


def _validate_examples() -> list[str]:
    errors: list[str] = []
    for name in COMMANDS:
        schema = discover(name)["input_schema"]
        try:
            jsonschema.validate(example_input(name), schema)
        except jsonschema.ValidationError as error:
            errors.append(f"input example for {name} is invalid: {error.message}")
    return errors


def _runbook_block(path: Path) -> tuple[str | None, str | None]:
    if not path.is_file():
        # The runbook is a local, unversioned artifact; absence is not drift.
        return None, None
    text = path.read_text(encoding="utf-8")
    if text.count(BEGIN_MARKER) != 1 or text.count(END_MARKER) != 1:
        return None, "agent runbook generated markers are missing or duplicated"
    expected = agent_reference().strip("\n")
    beginning = text.index(BEGIN_MARKER) + len(BEGIN_MARKER)
    ending = text.index(END_MARKER)
    actual = text[beginning:ending].strip("\n")
    if actual != expected:
        return actual, "agent runbook generated block is stale; run --write"
    return actual, None


def check_generated_docs(runbook: Path | None = None) -> list[str]:
    errors = check_markdown(render_markdown()) + _validate_examples()
    _, runbook_error = _runbook_block(runbook or AGENT_RUNBOOK)
    if runbook_error:
        errors.append(runbook_error)
    return errors


def write_runbook(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(BEGIN_MARKER) != 1 or text.count(END_MARKER) != 1:
        raise ValueError("agent runbook generated markers are missing or duplicated")
    beginning = text.index(BEGIN_MARKER) + len(BEGIN_MARKER)
    ending = text.index(END_MARKER)
    path.write_text(
        text[:beginning] + "\n" + agent_reference() + "\n" + text[ending:],
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="rewrite the agent runbook block")
    parser.add_argument("--check", action="store_true", help="fail when generated docs drift")
    args = parser.parse_args(argv)
    if args.write:
        write_runbook(AGENT_RUNBOOK)
    if args.check:
        errors = check_generated_docs()
        for error in errors:
            print(error, file=sys.stderr)
        return 1 if errors else 0
    if not args.write:
        print(render_markdown())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
