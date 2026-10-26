"""CLI parity with the TUI: inline session intent and a key file on `profile set`."""

from __future__ import annotations

from imagen_cli.application.container import Container


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
