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


def test_region_items_keep_their_commas_in_the_palette():
    arguments = _convert_arguments(
        "watermark erase",
        {"artifact": "art_" + "a" * 32,
         "regions": "4,8,16,12\n40,8,16,12"},
    )
    assert arguments["regions"] == ["4,8,16,12", "40,8,16,12"]
