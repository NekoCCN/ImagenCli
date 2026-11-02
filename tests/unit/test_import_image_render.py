"""`session import-image` reports a session id, not a session row."""

import io

from rich.console import Console

from imagen_cli.cli.human import _rich, render

SESSION = "ses_" + "a" * 32
ARTIFACT = "art_" + "b" * 32


def test_an_import_reports_the_new_artifact_and_session():
    text = render(_import(), columns=100)
    assert ARTIFACT in text and SESSION in text
    assert '"session"' not in text  # not the raw JSON fallback


def test_an_import_also_renders_on_a_terminal():
    stream = io.StringIO()
    _rich(_import(), Console(file=stream, force_terminal=True, width=100, highlight=False))
    text = stream.getvalue()
    assert ARTIFACT in text and SESSION in text


def _import() -> dict:
    return {
        "ok": True,
        "status": "succeeded",
        "data": {
            "session": SESSION,
            "artifact": {"id": ARTIFACT, "origin_manifest": {"kind": "imported"}},
            "path": f"/w/artifacts/{ARTIFACT}.image",
        },
    }
