"""Usage errors name the cause and the next step instead of a generic failure."""

import io
import json
import re

import pytest

from imagen_cli.cli.entrypoint import main
from imagen_cli.cli.human import render


def invoke(*args: str) -> tuple[int, dict]:
    out = io.StringIO()
    code = main([*args, "--output", "json", "--lang", "en"], stdout=out, stderr=io.StringIO())
    return code, json.loads(out.getvalue())


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (("sessoin", "list"), "sessoin"),
        (("generate", "--nope", "x"), "--nope"),
        (("generate", "--count"), "--count"),
        (("session", "show", "a", "b"), "session show"),
        (("help", "nosuch"), "nosuch"),
    ],
)
def test_usage_errors_name_the_offending_token(argv, expected):
    code, value = invoke(*argv)
    assert code == 2
    assert value["status"] == "validation_error"
    assert expected in value["error"]["message"]
    assert value["error"]["message"] != "命令未成功完成。"


def test_unknown_command_suggests_the_closest_command():
    _, value = invoke("sessoin", "list")
    commands = [action["command"] for action in value["error"]["next_actions"]]
    assert any("session" in command for command in commands)


def test_unknown_option_points_at_the_command_help():
    _, value = invoke("generate", "--nope", "x")
    assert "--nope" in value["error"]["message"]
    assert value["error"]["next_actions"] == [
        {"command": "imagen help generate", "creates_paid_request": False}
    ]


def test_schema_violation_describes_field_and_constraint():
    _, value = invoke("sheet", "--artifacts", "x", "--columns", "99")
    message = value["error"]["message"]
    assert "--columns" in message
    assert "10" in message


def test_duplicate_option_is_named():
    _, value = invoke("generate", "--count", "1", "--count", "2")
    assert "--count" in value["error"]["message"]


def test_human_error_shows_reason_and_next_step_inside_the_width():
    value = {
        "ok": False,
        "status": "validation_error",
        "error": {
            "type": "validation_error",
            "message": "Unknown option --nope. Run `imagen help generate` to list its options.",
            "retryable": False,
            "accepted_maybe": False,
            "next_actions": [{"command": "imagen help generate", "creates_paid_request": False}],
        },
    }
    for color in (False, True):
        text = render(value, columns=40, color=color)
        visible = re.sub(r"\x1b\[[0-9;]*m", "", text)
        assert "--nope" in visible
        assert "imagen help generate" in visible
        assert "…" not in visible
        assert all(len(line) <= 40 for line in visible.splitlines())


def test_human_error_never_repeats_a_generic_sentence():
    value = {
        "ok": False,
        "status": "validation_error",
        "error": {
            "type": "validation_error",
            "message": "Unknown command 'sessoin'. Did you mean 'session'?",
            "retryable": False,
            "accepted_maybe": False,
            "next_actions": [{"command": "imagen session", "creates_paid_request": False}],
        },
    }
    text = render(value, columns=80)
    assert "sessoin" in text
    assert "session" in text
