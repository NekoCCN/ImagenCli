from __future__ import annotations

from collections.abc import Iterable

from imagen_cli.domain.contracts import JsonObject
from imagen_cli.domain.models import CapabilityRecord


class CapabilityCatalog:
    def __init__(self, records: Iterable[CapabilityRecord | JsonObject]) -> None:
        self._records = tuple(
            record
            if isinstance(record, CapabilityRecord)
            else CapabilityRecord.model_validate(record)
            for record in records
        )

    def resolve(
        self, provider: str, family: str, model: str, model_revision: str, operation: str
    ) -> CapabilityRecord:
        for record in self._records:
            if (
                record.provider == provider
                and record.family == family
                and record.model == model
                and record.model_revision == model_revision
                and record.operation == operation
            ):
                return record
        raise ValueError("unsupported capability: no declaration")

    def records(self) -> tuple[CapabilityRecord, ...]:
        return self._records
