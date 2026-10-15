"""Creation sources must agree before any storage or provider work."""

import json

import pytest

from imagen_cli.cli.creation_input import parse_creation


def test_file_prompt_preserves_text_and_order(tmp_path):
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("  修改\n", encoding="utf-8")
    creation = parse_creation("edit", {
        "session": "poster", "prompt_file": str(prompt),
        "inputs": ["file:first.png", "file:second.png"], "count": 2,
    })
    assert creation.prompt == "  修改\n"
    assert creation.inputs == ["file:first.png", "file:second.png"]
    assert creation.count == 2


def test_cli_accepts_explicit_structured_stdin():
    from unittest.mock import patch

    from imagen_cli.cli.entrypoint import _parse

    with patch("imagen_cli.cli.creation_input.read_json", return_value={
        "schema_version": 1, "operation": "generate", "session": "poster", "prompt": "image",
    }):
        command, data, _ = _parse(["generate", "--input-json", "-"])
    assert command == "generate"
    assert data["prompt"] == "image"


def test_structured_input_requires_matching_operation(tmp_path):
    source = tmp_path / "request.json"
    source.write_text(json.dumps({
        "schema_version": 1, "operation": "generate", "session": "poster", "prompt": "蓝天",
    }), encoding="utf-8")
    assert parse_creation("generate", {"input_json": str(source)}).prompt == "蓝天"
    with pytest.raises(ValueError):
        parse_creation("edit", {"input_json": str(source)})
    with pytest.raises(ValueError):
        parse_creation("generate", {"input_json": str(source), "count": 1})


def test_provider_parameters_use_bounded_strict_json_reader(tmp_path):
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("image", encoding="utf-8")
    params = tmp_path / "params.json"
    params.write_text('{"quality":"high","quality":"low"}', encoding="utf-8")
    with pytest.raises(ValueError):
        parse_creation("generate", {
            "session": "poster", "prompt_file": str(prompt),
            "provider_params_file": str(params),
        })


@pytest.mark.parametrize("parameters", [
    {"session": "poster"},
    {"session": "poster", "prompt": "implicit prompt is not a CLI source"},
    {"session": "poster", "prompt_file": "missing", "prompt_stdin": True},
    {"input_json": "missing", "model": "override"},
])
def test_invalid_sources_fail_before_reading_files(parameters):
    with pytest.raises(ValueError):
        parse_creation("generate", parameters)
