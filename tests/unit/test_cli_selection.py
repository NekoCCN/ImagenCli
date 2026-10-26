import io

from imagen_cli.cli import selection
from imagen_cli.cli.selection import choose


def test_selection_cancel_and_multiple():
    err = io.StringIO()
    assert choose(["codex", "cline"], stdin=io.StringIO("1,2\n"), stderr=err) == ["codex", "cline"]
    assert choose(["codex"], stdin=io.StringIO("\n"), stderr=err) == []
    assert choose(["codex"], stdin=io.StringIO("99\n"), stderr=err) == []


def test_space_toggles_the_highlighted_row():
    picked, cursor, done = selection.apply(["universal", "claude-code"], ["universal"], 1, " ")
    assert picked == ["universal", "claude-code"]
    assert cursor == 1
    assert done is None
    picked, _, _ = selection.apply(["universal"], ["universal"], 0, " ")
    assert picked == []


def test_enter_confirms_and_escape_cancels():
    assert selection.apply(["a", "b"], ["b"], 0, "\r") == (["b"], 0, True)
    assert selection.apply(["a"], ["a"], 0, "\x1b") == ([], 0, False)


def test_a_toggles_every_row():
    assert selection.apply(["a", "b"], ["a"], 0, "a")[0] == ["a", "b"]
    assert selection.apply(["a", "b"], ["a", "b"], 0, "a")[0] == []


def test_arrows_move_and_wrap():
    assert selection.apply(["a", "b"], [], 0, "\x1b[B")[1] == 1
    assert selection.apply(["a", "b"], [], 1, "\x1b[B")[1] == 0
    assert selection.apply(["a", "b"], [], 0, "\x1b[A")[1] == 1
    assert selection.apply(["a", "b"], [], 1, "k")[1] == 0
    assert selection.apply(["a", "b"], [], 0, "j")[1] == 1


def test_an_empty_selection_installs_the_highlighted_row():
    assert selection.apply(["universal"], [], 0, "\r")[0] == ["universal"]


def test_the_picker_walks_the_keys_and_returns_the_ticks(monkeypatch):
    keys = iter(["\x1b[B", " ", "\r"])
    monkeypatch.setattr(selection, "_tty", lambda stream: True)
    monkeypatch.setattr(selection, "_read_key", lambda stream: next(keys))
    picked = selection.choose(
        ["universal", "aider-desk"],
        stdin=io.StringIO(),
        stderr=io.StringIO(),
        selected=["universal"],
    )
    assert picked == ["universal", "aider-desk"]


def test_the_picker_cancels_on_q(monkeypatch):
    monkeypatch.setattr(selection, "_tty", lambda stream: True)
    monkeypatch.setattr(selection, "_read_key", lambda stream: "q")
    err = io.StringIO()
    assert selection.choose(["universal"], stdin=io.StringIO(), stderr=err) == []
