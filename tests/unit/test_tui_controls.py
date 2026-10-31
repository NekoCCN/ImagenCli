"""The TUI must reach the same run options the CLI takes as global flags."""

import pytest

pytest.importorskip("textual")

from imagen_cli.cli.tui.app import (  # noqa: E402
    _control_fields,
    _control_options,
    _convert_controls,
    _needs_form,
)


def test_generate_offers_async_dry_run_wait_and_timeout():
    names = [field[0] for field in _control_fields("generate")]
    assert names == ["async", "dry_run", "wait", "timeout"]


def test_commands_without_controls_offer_nothing():
    assert _control_fields("session list") == []


def test_blank_controls_are_skipped_and_the_rest_are_typed():
    assert _convert_controls(
        {"async": "true", "dry_run": "", "wait": "false", "timeout": "60"}
    ) == {"async_": True, "wait": False, "timeout": 60}


def test_job_wait_offers_only_timeout():
    assert [field[0] for field in _control_fields("job wait")] == ["timeout"]


def test_optional_fields_still_get_a_form():
    # `asset gc` has only optional fields; running it blind fails with plan/yes required.
    assert _needs_form("asset gc") is True
    # A paging-only command runs straight away instead of asking for limit/cursor.
    assert _needs_form("session list") is False


def test_control_options_are_validated_like_the_cli():
    with pytest.raises(ValueError):
        _control_options(None, {"wait": True, "async_": True})
    with pytest.raises(ValueError):
        _control_options(None, {"timeout": -1})
