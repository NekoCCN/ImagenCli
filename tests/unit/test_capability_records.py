"""Canonical capability digests are stable and duplicate routes are rejected."""

import pytest
from pydantic import ValidationError

from imagen_cli.capabilities.records import build, compute_digest, unique
from tests.unit.test_capabilities import capability


def test_digest_is_order_independent_and_content_sensitive():
    data = capability()
    reordered = dict(reversed(list(data.items())))
    assert compute_digest(data) == compute_digest(reordered)
    assert compute_digest({**data, "model_revision": "2026-02"}) != compute_digest(data)


def test_build_populates_the_digest_and_rejects_a_bad_status():
    assert build(capability()).digest == compute_digest(capability())
    with pytest.raises(ValidationError):
        build({**capability(), "support_status": "Z"})


def test_unique_rejects_same_route_revision_operation():
    record = build(capability())
    assert unique([record]) == (record,)
    with pytest.raises(ValueError):
        unique([record, record])
