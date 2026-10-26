"""Regression checks for malformed catalog entries and unsafe boolean coercion."""

import pytest

from imagen_cli.application.model_catalog_service import _identities
from imagen_cli.cli.tui.app import _convert_arguments, _convert_controls


def test_gemini_catalog_skips_invalid_entries():
    assert _identities("generate_content", {"models": [None, {}, {"name": "models/a"}, {}]}) == [
        {"model_id": "a", "display_name": "a"}
    ]


@pytest.mark.parametrize("raw", ["tru", "flase", "anything"])
def test_invalid_boolean_does_not_become_false(raw):
    with pytest.raises(ValueError):
        _convert_arguments("asset gc", {"dry_run": raw})
    with pytest.raises(ValueError):
        _convert_controls({"async": raw})
