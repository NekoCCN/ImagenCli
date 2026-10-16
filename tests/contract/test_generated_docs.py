"""T099: generated help/schema examples stay tied to the single CLI registry."""

import json

import jsonschema
import pytest

from imagen_cli.cli import generated_docs as docs
from imagen_cli.cli.registry import COMMANDS, discover


def test_generated_docs_match_registry_and_runbook() -> None:
    assert docs.check_generated_docs() == []
    text = docs.render_markdown()
    for name in COMMANDS:
        assert text.count(f"### `{name}`") == 1
        jsonschema.validate(docs.example_input(name), discover(name)["input_schema"])
    capability = docs.capability_example()
    jsonschema.validate(capability, discover("capabilities")["data_schema"])
    assert json.dumps(capability, ensure_ascii=False)


def test_command_help_is_derived_from_the_input_schema() -> None:
    help_text = docs.command_help("job wait")
    parameters = discover("job wait")["input_schema"]["properties"]
    assert "job_id" in parameters
    assert "job_id: string (required)" in help_text
    assert "timeout: integer (optional, default=300)" in help_text
    assert docs.command_help("session create").splitlines()[0].startswith("session create —")


def test_generated_docs_detect_textual_drift() -> None:
    text = docs.render_markdown()
    row = docs._parameter_rows("session create")[0]
    mutated = text.replace(f"| `session create` {row}", "")
    assert mutated != text
    errors = docs.check_markdown(mutated)
    assert any("session create" in error for error in errors)


def test_generated_docs_detect_stale_runbook(tmp_path) -> None:
    runbook = tmp_path / "agent-runbook.md"
    runbook.write_text(
        f"{docs.BEGIN_MARKER}\nstale table\n{docs.END_MARKER}\n", encoding="utf-8",
    )
    errors = docs.check_generated_docs(runbook)
    assert any("stale" in error for error in errors)

    docs.write_runbook(runbook)
    assert docs.check_generated_docs(runbook) == []


@pytest.mark.parametrize("name", ["help", "schema", "generate", "edit", "batch submit"])
def test_every_example_is_minimal_and_valid(name: str) -> None:
    example = docs.example_input(name)
    jsonschema.validate(example, discover(name)["input_schema"])
    assert set(example) <= set(discover(name)["input_schema"]["properties"])
