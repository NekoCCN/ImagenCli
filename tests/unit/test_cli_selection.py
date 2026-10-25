import io

from imagen_cli.cli.selection import choose


def test_selection_cancel_and_multiple():
    err = io.StringIO()
    assert choose(["codex", "cline"], stdin=io.StringIO("1,2\n"), stderr=err) == ["codex", "cline"]
    assert choose(["codex"], stdin=io.StringIO("\n"), stderr=err) == []
    assert choose(["codex"], stdin=io.StringIO("99\n"), stderr=err) == []
