"""CLI parity with the TUI: inline session intent and a key file on `profile set`."""

from __future__ import annotations

import io

from imagen_cli.application.container import Container
from imagen_cli.cli.entrypoint import main


def run(*args: str) -> tuple[int, str]:
    out = io.StringIO()
    code = main(list(args), stdout=out, stderr=io.StringIO())
    return code, out.getvalue()


def test_the_cli_parser_accepts_intent(tmp_path):
    code, text = run(
        "--workspace", str(tmp_path / "ws"), "session", "create", "Inline",
        "--intent", "keep it calm",
    )
    assert code == 0
    assert "Inline" in text


def test_missing_reference_and_key_file_name_both_options(tmp_path):
    code, text = run(
        "--workspace", str(tmp_path / "ws"), "profile", "set", "local",
        "--provider", "openai", "--family", "images",
        "--base-url", "http://127.0.0.1:1", "--model-default", "m",
    )
    assert code == 2
    assert "credential" in text
    assert "key-file" in text


def test_session_create_accepts_intent_inline(tmp_path):
    with Container(workspace=str(tmp_path / "ws")) as container:
        status, data = container.execute(
            "session create", {"name": "Inline", "intent": "keep it calm"}
        )
    assert status == "succeeded"
    assert data["session"]["intent"] == "keep it calm"


def test_profile_set_stores_a_key_file_without_echoing_it(tmp_path):
    key = tmp_path / "key.txt"
    key.write_text("sk-not-a-real-key\n", encoding="utf-8")
    with Container(workspace=str(tmp_path / "ws")) as container:
        status, data = container.execute(
            "profile set",
            {
                "profile": "local",
                "provider": "openai",
                "family": "images",
                "base_url": "http://127.0.0.1:1",
                "model_default": "m",
                "key_file": str(key),
            },
        )
        row = container.repositories.get("profiles", "local", include_private=True)
    assert status == "succeeded"
    assert row is not None
    assert str(row["credential_ref"]).startswith("private:")
    assert "sk-not-a-real-key" not in str(data)
