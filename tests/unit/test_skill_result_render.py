"""Skill install/update/uninstall results render as lines, not a JSON dump."""

from imagen_cli.cli.human import render


def value(data: dict) -> dict:
    return {"ok": True, "status": "succeeded", "data": data}


def test_install_and_uninstall_results_are_readable():
    text = render(
        value({"installed": [{"name": "imagen", "path": "/p/imagen"}]}), columns=100
    )
    assert "imagen" in text and "/p/imagen" in text
    assert '"installed"' not in text  # not the raw JSON fallback
    text = render(value({"removed": [{"name": "imagen", "path": "/p/imagen"}]}), columns=100)
    assert "/p/imagen" in text and '"removed"' not in text


def test_a_conflict_still_lists_what_was_skipped():
    text = render(
        {
            "ok": False,
            "status": "conflict",
            "data": {"existing": [{"name": "imagen", "path": "/p/imagen"}]},
        },
        columns=100,
    )
    assert "imagen" in text and "/p/imagen" in text
    assert "--force" in text
