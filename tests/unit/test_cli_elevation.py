"""Permission failures name the fix and ask before any Windows elevation."""

import errno
import io
import json
import sys

import pytest

from imagen_cli.cli import elevation, entrypoint


class _WinError(OSError):
    def __init__(self, code: int) -> None:
        super().__init__(f"winerror {code}")
        self.winerror = code


def test_permission_errors_are_recognized():
    assert elevation.is_permission_denied(PermissionError(errno.EACCES, "denied"))
    assert elevation.is_permission_denied(OSError(errno.EACCES, "denied"))
    assert elevation.is_permission_denied(OSError(errno.EPERM, "denied"))
    assert elevation.is_permission_denied(_WinError(5))
    assert not elevation.is_permission_denied(OSError(errno.ENOENT, "missing"))


def test_windows_symlink_privilege_explains_developer_mode():
    error = _WinError(1314)
    assert elevation.is_permission_denied(error)
    assert "开发者模式" in elevation.message(error)


def test_command_line_keeps_the_invocation():
    line = elevation.command_line(["skills", "install", "--target", "claude-code"])
    assert line.startswith("imagen skills install")
    assert "claude-code" in line


class _TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


class _Stdin:
    def __init__(self, answer: str) -> None:
        self.answer = answer

    def isatty(self) -> bool:
        return True

    def readline(self) -> str:
        return self.answer


class _FailingContainer:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def execute(self, command, args):
        raise PermissionError(errno.EACCES, "denied")


@pytest.fixture
def denied(monkeypatch):
    monkeypatch.setattr(entrypoint, "Container", _FailingContainer)
    monkeypatch.setattr(elevation, "supported", lambda: True)
    monkeypatch.setattr(
        elevation, "relaunch", lambda args: pytest.fail("relaunch must be explicit")
    )
    return monkeypatch


def test_confirmed_retry_relaunches_elevated(denied):
    calls = []
    denied.setattr(elevation, "relaunch", lambda args: calls.append(args) or 42)
    denied.setattr(sys, "stdin", _Stdin("y\n"))
    stdout, stderr = _TTY(), _TTY()

    code = entrypoint.main(["--zh", "session", "list"], stdout=stdout, stderr=stderr)

    assert code == 42
    assert calls == [["--zh", "session", "list"]]
    assert "管理员" in stderr.getvalue()


def test_default_answer_renders_permission_denied(denied):
    denied.setattr(sys, "stdin", _Stdin("\n"))
    stdout, stderr = _TTY(), _TTY()

    code = entrypoint.main(["session", "list"], stdout=stdout, stderr=stderr)

    assert code == 3
    assert "权限" in stdout.getvalue()


def test_machine_output_never_asks_for_elevation(denied):
    denied.setattr(sys, "stdin", _Stdin("y\n"))
    stdout, stderr = _TTY(), _TTY()

    code = entrypoint.main(
        ["--output", "json", "session", "list"], stdout=stdout, stderr=stderr
    )

    payload = json.loads(stdout.getvalue())
    assert code == 3
    assert payload["status"] == "permission_denied"
    assert payload["error"]["next_actions"][0]["requires_elevation"] is True
    assert stderr.getvalue() == ""


def test_no_interactive_never_asks(denied):
    denied.setattr(sys, "stdin", _Stdin("y\n"))
    stdout, stderr = _TTY(), _TTY()

    code = entrypoint.main(
        ["--no-interactive", "session", "list"], stdout=stdout, stderr=stderr
    )

    assert code == 3
    assert stderr.getvalue() == ""
