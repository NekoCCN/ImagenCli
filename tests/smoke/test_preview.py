"""Preview detection is optional and failure-isolated."""

import pytest

from imagen_cli.cli.preview import preview, supports_preview


class _Stream:
    def __init__(self, tty=True, fail=False):
        self.tty, self.fail, self.text = tty, fail, ""

    def isatty(self):
        return self.tty

    def write(self, value):
        if self.fail:
            raise OSError("broken preview")
        self.text += value

    def flush(self):
        pass


def test_preview_requires_a_capable_terminal(tmp_path, monkeypatch):
    target = tmp_path / "image.png"
    target.write_bytes(b"x")
    monkeypatch.setenv("TERM", "dumb")
    assert not supports_preview(_Stream())
    assert preview(target, _Stream()) is False


def test_preview_failure_is_isolated(tmp_path, monkeypatch):
    target = tmp_path / "image.png"
    target.write_bytes(b"x")
    monkeypatch.setenv("TERM", "xterm-256color")
    stream = _Stream(fail=True)
    assert supports_preview(stream)
    assert preview(target, stream) is False


def test_preview_can_be_disabled_by_environment(tmp_path, monkeypatch):
    target = tmp_path / "image.png"
    target.write_bytes(b"x")
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("IMAGEN_PREVIEW", "0")
    assert preview(target, _Stream()) is False


@pytest.mark.parametrize("name", ["missing.png"])
def test_missing_files_are_not_previewed(name, tmp_path, monkeypatch):
    monkeypatch.setenv("TERM", "xterm-256color")
    assert preview(tmp_path / name, _Stream()) is False
