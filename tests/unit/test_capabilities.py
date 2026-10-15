from __future__ import annotations

import pytest

from imagen_cli.capabilities.catalog import CapabilityCatalog
from imagen_cli.capabilities.validator import CapabilityValidator


def capability(**changes):
    value = {
        "id": "openai-images-generate",
        "provider": "openai",
        "family": "images",
        "model": "image-model",
        "model_revision": "2026-01",
        "operation": "generate",
        "support_status": "I",
        "parameter_schema": {
            "type": "object",
            "properties": {"size": {"type": "string"}},
            "additionalProperties": False,
        },
        "semantic_mapping": {"prompt": "prompt"},
        "input_limits": {"max_count": 1},
        "transport_kind": "sync",
        "continuation_strategy": "none",
        "idempotency_contract": {},
        "cancel_contract": {},
        "sources": [{
            "url": "https://docs.example.test", "date": "2026-10-23", "locator": "fixture"
        }],
        "checked_at": "2026-10-23T00:00:00Z",
        "offline_evidence": [{"kind": "fixture"}],
        "live_evidence": [],
        "digest": "a" * 64,
    }
    value.update(changes)
    return value


def test_catalog_loads_records_and_selects_exact_model_revision_operation():
    catalog = CapabilityCatalog([capability()])
    record = catalog.resolve("openai", "images", "image-model", "2026-01", "generate")
    assert record.id == "openai-images-generate"


def test_validator_rejects_undeclared_operation_model_and_parameter():
    validator = CapabilityValidator(CapabilityCatalog([capability()]))
    for args in (
        ("openai", "images", "image-model", "2026-01", "edit", {}),
        ("openai", "images", "other-model", "2026-01", "generate", {}),
        ("openai", "images", "image-model", "2026-01", "generate", {"quality": "high"}),
    ):
        with pytest.raises(ValueError, match="unsupported capability"):
            validator.validate(*args)


def test_validator_accepts_only_declared_parameters():
    validator = CapabilityValidator(CapabilityCatalog([capability()]))
    decision = validator.validate(
        "openai", "images", "image-model", "2026-01", "generate", {"size": "1024x1024"}
    )
    assert decision.supported is True
    assert decision.capability_id == "openai-images-generate"
