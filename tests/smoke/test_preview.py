"""Preview detection is optional and failure-isolated."""

import pytest
from PIL import Image

from imagen_cli.cli.preview import preview, supports_preview
from imagen_cli.cli.tui.preview import ansi_image


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


@pytest.mark.parametrize(
    ("size", "cols", "rows", "width", "height"),
    [((16, 8), 16, 16, 16, 4), ((8, 16), 16, 8, 8, 8), ((8, 1), 8, 1, 8, 1)],
)
def test_ansi_preview_preserves_aspect_ratio(size, cols, rows, width, height):
    from rich.text import Text

    rendered = Text.from_ansi(ansi_image(Image.new("RGB", size, "red"), cols=cols, rows=rows))
    lines = rendered.plain.splitlines()
    assert len(lines) == height
    assert all(len(line) == width for line in lines)


def test_ansi_preview_composites_alpha_without_tinting_opaque_colors():
    from rich.text import Text

    image = Image.new("RGBA", (2, 2), (255, 0, 0, 255))
    image.putpixel((1, 0), (0, 255, 0, 0))
    rendered = Text.from_ansi(ansi_image(image, cols=2, rows=1))
    colors = [span.style.color.triplet for span in rendered.spans]
    assert colors == [(255, 0, 0), (9, 11, 16)]


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
