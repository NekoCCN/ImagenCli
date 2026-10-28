"""One catalog feeds the CLI prompt and the TUI form; neither builds its own labels."""

from imagen_cli.application.superres import MANIFEST, fetch_choices


def test_fetch_choices_covers_the_manifest_with_its_provenance():
    choices = fetch_choices()
    assert [name for _, name in choices] == list(MANIFEST)
    for label, name in choices:
        entry = MANIFEST[name]
        assert label.startswith(f"{name}: {entry['scale']}x | {entry['license']} | ")
        assert entry["source"] in label


def test_fetch_choices_labels_stay_unique():
    labels = [label for label, _ in fetch_choices()]
    assert len(set(labels)) == len(labels)


def test_the_cli_picker_lists_the_shared_catalog(monkeypatch, tmp_path):
    """The prompt and the TUI form must offer the same rows in the same order."""
    import io
    import sys
    from pathlib import Path

    from imagen_cli.cli import entrypoint

    seen: dict[str, list[str]] = {}

    class TTY(io.StringIO):
        def isatty(self) -> bool:
            return True

    def fake_choose(labels, **kwargs):
        seen["labels"] = list(labels)
        return []

    monkeypatch.setattr("imagen_cli.cli.selection.choose", fake_choose)
    monkeypatch.setattr("imagen_cli.storage.workspace.user_data_path", lambda name: Path(tmp_path))
    monkeypatch.setattr(sys, "stdin", TTY())
    entrypoint.main(["superres", "fetch"], stdout=TTY(), stderr=TTY())
    assert seen["labels"] == [label for label, _ in fetch_choices()]
