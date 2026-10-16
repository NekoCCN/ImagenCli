"""Offline machine boundaries; expectations come from the published contracts."""

import io
import json
from pathlib import Path

import jsonschema
import pytest
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "tests" / "fixtures" / "contracts"
EXAMPLES = json.loads((CONTRACTS / "examples.json").read_text(encoding="utf-8"))


def validator(name):
    schemas = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in CONTRACTS.glob("*.schema.json")
    ]
    registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas)
    schema = json.loads((CONTRACTS / name).read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(
        schema, registry=registry, format_checker=jsonschema.FormatChecker()
    )


def test_every_published_example_and_renderer():
    from imagen_cli.cli.input_readers import read_json
    from imagen_cli.cli.renderers import Renderer

    for value in EXAMPLES["creation_inputs"]:
        assert read_json(io.BytesIO(json.dumps(value).encode())) == value
        validator("creation-input.schema.json").validate(value)
    for value in EXAMPLES["invalid_creation_inputs"]:
        assert not validator("creation-input.schema.json").is_valid(value)
    for value in EXAMPLES["results"]:
        out = io.StringIO()
        code = Renderer("json", out, io.StringIO()).finish(value)
        assert json.loads(out.getvalue()) == value
        validator("result.schema.json").validate(value)
        assert (
            code
            == {
                "succeeded": 0,
                "accepted": 0,
                "unknown": 6,
                "partial_success": 9,
                "unsupported_capability": 2,
            }[value["status"]]
        )
    for events in EXAMPLES["jsonl_invocations"]:
        out = io.StringIO()
        renderer = Renderer("jsonl", out, io.StringIO())
        for event in events:
            renderer.emit_event(event)
        assert [json.loads(line) for line in out.getvalue().splitlines()] == events
        for event in events:
            validator("event.schema.json").validate(event)
        with pytest.raises(ValueError):
            renderer.emit_event(events[-1])


@pytest.mark.parametrize(
    "raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":"\\u0000"}', b"\xff", b"[]"]
)
def test_json_rejects_ambiguous_or_unsafe_input(raw):
    from imagen_cli.cli.input_readers import read_json

    with pytest.raises(ValueError):
        read_json(io.BytesIO(raw))


def test_text_limits_encoding_and_preservation():
    from imagen_cli.cli.input_readers import read_json, read_text

    assert read_text(io.BytesIO(" 海报\n".encode())) == " 海报\n"
    assert len(read_text(io.BytesIO(b"x" * 1048576))) == 1048576
    for raw in (b"x" * 1048577, b"\0", b"\xff", b"  \n"):
        with pytest.raises(ValueError):
            read_text(io.BytesIO(raw))
    with pytest.raises(ValueError):
        read_json(io.BytesIO(b" " * (4194304 + 1)))


def test_stdin_is_explicit_and_deadline_is_enforced(monkeypatch):
    from imagen_cli.cli.input_readers import read_prompt, read_stdin

    class Forbidden:
        def read(self, *args):
            raise AssertionError("implicit stdin read")

    monkeypatch.setattr("sys.stdin", Forbidden())
    with pytest.raises(ValueError):
        read_prompt()
    import os

    read_fd, write_fd = os.pipe()
    try:
        with os.fdopen(read_fd, "rb", buffering=0) as stream:
            with pytest.raises(ValueError, match="timeout"):
                read_stdin(stream, timeout=0.01)
    finally:
        os.close(write_fd)


@pytest.mark.parametrize(
    "options",
    [
        {"limit": True},
        {"timeout": True},
        {"timeout": float("nan")},
        {"limit": 1001},
        {"cursor": "bad!"},
        {"wait": True, "async_": True},
    ],
)
def test_shared_options_reject_invalid_values(options):
    from imagen_cli.cli.options import SharedOptions

    with pytest.raises(ValueError):
        SharedOptions(**options)


def test_redaction_and_terminal_rules():
    from imagen_cli.cli.diagnostics import redact
    from imagen_cli.cli.renderers import Renderer, result

    value = redact(
        {
            "prompt": "secret-prompt",
            "api_key": "secret-key",
            "nested": {"url": "https://example.org/image?sig=secret"},
            "message": "Bearer secret-token",
        }
    )
    assert "secret" not in json.dumps(value)
    out = io.StringIO()
    renderer = Renderer("jsonl", out, io.StringIO())
    renderer.start("schema")
    renderer.finish(result("validation_error"))
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [line["seq"] for line in lines] == [1, 2]
    assert [line["event"] for line in lines] == ["started", "terminal"]
    for line in lines:
        validator("event.schema.json").validate(line)
    with pytest.raises(ValueError):
        renderer.finish(result())


def test_failed_result_uses_provider_error_type():
    from imagen_cli.cli.renderers import result

    validator("result.schema.json").validate(result("failed"))


def test_stdin_supports_redirected_regular_files(tmp_path):
    from imagen_cli.cli.input_readers import read_stdin

    source = tmp_path / "prompt.txt"
    source.write_bytes(b"redirected prompt")
    with source.open("rb") as stream:
        assert read_stdin(stream) == b"redirected prompt"


def test_event_shape_rejected_before_emission():
    from imagen_cli.cli.renderers import Renderer

    out = io.StringIO()
    renderer = Renderer("jsonl", out, io.StringIO())
    with pytest.raises(ValueError):
        renderer.emit_event({"seq": 1, "event": "terminal"})
    assert out.getvalue() == ""


def test_discovery_schema_remains_usable_after_rendering():
    from imagen_cli.cli.entrypoint import main

    out = io.StringIO()
    assert main(["schema", "generate"], stdout=out, stderr=io.StringIO()) == 0
    schema = json.loads(out.getvalue())["data"]["input_schema"]
    jsonschema.Draft202012Validator.check_schema(schema)
