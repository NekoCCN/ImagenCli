"""Declared provider idempotency and cancellation; silence is never a guarantee."""

from dataclasses import dataclass

__all__ = ["ProviderContract", "contract", "claims_exactly_once"]


@dataclass(frozen=True)
class ProviderContract:
    provider: str
    family: str
    transport: str = "sync"
    idempotency_scope: str = "unknown"
    idempotency_window_s: int | None = None
    cancellation: str = "unsupported"


_CONTRACTS = {
    ("openai", "images"): ProviderContract(
        "openai", "images", idempotency_scope="none",
    ),
    ("gemini", "generate_content"): ProviderContract(
        "gemini", "generate_content", idempotency_scope="none",
    ),
}


def contract(provider: str, family: str) -> ProviderContract:
    return _CONTRACTS.get((provider, family), ProviderContract(provider, family))


def claims_exactly_once(value: ProviderContract) -> bool:
    """Only a declared request key with a bounded window supports the claim."""
    return value.idempotency_scope == "request_key" and bool(value.idempotency_window_s)
