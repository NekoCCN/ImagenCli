"""T/I/D/N/U declarations are distinct and evidence-backed."""

import pytest
from pydantic import ValidationError

from imagen_cli.capabilities.records import build, compute_digest, unique
from tests.unit.test_capabilities import capability


@pytest.mark.parametrize("status", ["T", "I", "D", "N", "U"])
def test_every_status_is_representable_and_distinct(status):
    data = capability(support_status=status)
    if status == "T":
        data = capability(support_status=status, live_evidence=[{"kind": "live"}])
    record = build(data)
    assert str(record.support_status) == status
    assert record.digest == compute_digest(data)


def test_tested_requires_live_evidence():
    with pytest.raises(ValidationError):
        build(capability(support_status="T"))


def test_declaration_requires_a_source():
    with pytest.raises(ValidationError):
        build(capability(sources=[]))


def test_duplicate_model_revision_operation_is_rejected():
    first = build(capability())
    with pytest.raises(ValueError):
        unique([first, first])


def test_digest_covers_content_and_ignores_the_digest_field():
    data = capability()
    digest = compute_digest(data)
    assert compute_digest({**data, "digest": "b" * 64}) == digest
    assert compute_digest({**data, "model": "other"}) != digest
