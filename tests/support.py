"""Shared test fixtures."""

from __future__ import annotations


def capability(**changes):
    """An inert capability row for storage-level tests."""
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
