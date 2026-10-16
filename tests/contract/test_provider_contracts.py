"""Silence is not a guarantee: unknown providers claim neither dedupe nor cancel."""

from dataclasses import replace

from imagen_cli.providers.contracts import ProviderContract, claims_exactly_once, contract


def test_unknown_provider_never_claims_exactly_once_or_remote_cancel():
    value = contract("mystery", "images")
    assert value.idempotency_scope == "unknown"
    assert value.idempotency_window_s is None
    assert value.cancellation == "unsupported"
    assert not claims_exactly_once(value)


def test_only_a_declared_key_and_window_claims_exactly_once():
    declared = ProviderContract(
        "x", "y", idempotency_scope="request_key", idempotency_window_s=60,
    )
    assert claims_exactly_once(declared)
    assert not claims_exactly_once(replace(declared, idempotency_window_s=None))
    assert not claims_exactly_once(replace(declared, idempotency_scope="unknown"))


def test_openai_images_declares_no_idempotency():
    value = contract("openai", "images")
    assert value.idempotency_scope == "none"
    assert not claims_exactly_once(value)
