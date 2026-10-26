"""`--version`, and a session view that names artifacts and unknown outcomes."""

from __future__ import annotations

import io
import json

from imagen_cli.cli.entrypoint import main
from imagen_cli.cli.human import render


def run(*args: str) -> tuple[int, str]:
    out = io.StringIO()
    code = main(list(args), stdout=out, stderr=io.StringIO())
    return code, out.getvalue()


def test_version_flag_answers_without_touching_a_workspace(tmp_path):
    missing = tmp_path / "never-created"
    code, text = run("--workspace", str(missing), "--version")
    assert code == 0
    assert "imagen" in text.lower()
    assert not missing.exists()


def test_version_flag_fills_the_json_envelope():
    code, text = run("--output", "json", "--version")
    assert code == 0
    value = json.loads(text)
    assert value["ok"] is True
    assert value["data"]["version"]


def session_value() -> dict[str, object]:
    return {
        "ok": True,
        "status": "succeeded",
        "data": {
            "session": {"id": "ses_" + "a" * 32, "name": "Test", "version": 2},
            "turns": [
                {
                    "id": "trn_" + "1" * 32,
                    "operation": "generate",
                    "creation_state": "succeeded",
                    "job_id": "job_" + "1" * 32,
                },
                {
                    "id": "trn_" + "2" * 32,
                    "operation": "edit",
                    "creation_state": "unknown",
                    "job_id": "job_" + "2" * 32,
                },
            ],
            "references": [
                {
                    "turn_id": "trn_" + "2" * 32,
                    "artifact_id": "art_" + "b" * 32,
                    "role": "output",
                    "index": 0,
                }
            ],
        },
    }


def test_session_view_names_the_artifact_each_turn_produced():
    for color in (False, True):
        text = render(session_value(), columns=100, color=color)
        assert "art_" + "b" * 32 in text


def test_session_view_says_what_to_do_about_an_unknown_outcome():
    for color in (False, True):
        text = render(session_value(), columns=120, color=color)
        assert "job_" + "2" * 32 in text


def test_known_outcomes_get_no_hint():
    text = render(session_value(), columns=120, color=False)
    assert "job_" + "1" * 32 not in text
