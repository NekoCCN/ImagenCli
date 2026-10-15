from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator

from .catalog import CapabilityCatalog


@dataclass(frozen=True)
class CapabilityDecision:
    supported: bool
    capability_id: str
    digest: str


class CapabilityValidator:
    def __init__(self, catalog: CapabilityCatalog) -> None:
        self.catalog = catalog

    def validate(
        self,
        provider: str,
        family: str,
        model: str,
        model_revision: str,
        operation: str,
        parameters: dict[str, Any],
    ) -> CapabilityDecision:
        record = self.catalog.resolve(provider, family, model, model_revision, operation)
        if record.support_status.value in {"N", "U"}:
            raise ValueError("unsupported capability: declaration does not support operation")
        validator = Draft202012Validator(record.parameter_schema)
        errors = list(validator.iter_errors(parameters))
        if errors:
            raise ValueError("unsupported capability: parameters are not declared")
        return CapabilityDecision(True, record.id, record.digest)
