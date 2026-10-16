"""Non-TTY JSON and JSONL envelopes stay stable for every terminal status."""

import json

import pytest

from imagen_cli.cli.registry import discover
from imagen_cli.cli.renderers import Renderer, result

CODES = {"succeeded": 0, "validation_error": 2, "not_found": 4, "conflict": 5,
         "approval_required": 6, "unsupported_capability": 2, "unknown": 6,
         "partial_success": 9, "save_failed": 7}


@pytest.mark.parametrize("status", list(CODES))
@pytest.mark.parametrize("mode", ["json", "jsonl"])
def test_every_status_has_one_terminal_record(status, mode, capsys):
    value = result(status, {})
    code = Renderer(mode).finish(value)
    assert code == CODES[status]
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    if mode == "json":
        assert lines[0]["status"] == status
        assert lines[0]["ok"] is (status == "succeeded")
    else:
        assert lines[-1]["event"] == "terminal"
        assert lines[-1]["result"]["status"] == status


def test_help_differs_from_execution_availability():
    help_record = discover("generate")
    assert help_record["input_schema"] and help_record["output_schema"]


def test_closed_stdin_with_explicit_input_is_rejected_without_hanging(tmp_path):
    from imagen_cli.cli.entrypoint import main

    code = main([
        "--workspace", str(tmp_path), "--output", "json", "generate", "--stdin",
    ])
    assert code == 2
