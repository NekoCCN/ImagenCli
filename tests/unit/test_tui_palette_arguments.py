"""The `:` palette turns form strings into command arguments; booleans must survive."""

import pytest

pytest.importorskip("textual")

from imagen_cli.cli.tui.app import _convert_arguments  # noqa: E402


def test_const_true_confirmation_reaches_the_command_as_true():
    # `session delete --yes` and `asset gc --yes` declare `yes` as const true.
    arguments = _convert_arguments(
        "session delete", {"session": "ses_" + "a" * 32, "yes": "true"}
    )
    assert arguments["yes"] is True
