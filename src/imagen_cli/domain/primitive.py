"""Strict, JSON-safe domain primitives; no I/O or provider dependencies."""

import hashlib
import math
import re
from datetime import datetime
from typing import Annotated, Any
from uuid import uuid4

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]
NonEmptyString = Annotated[str, Field(strict=True, min_length=1)]
Sha256 = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]
FiniteNumber = Annotated[float, Field(strict=True, allow_inf_nan=False)]


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def validate_timestamp(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value):
        raise ValueError("timestamp must be UTC RFC3339 ending in Z")
    datetime.fromisoformat(value)
    return value


UtcTimestamp = Annotated[str, Field(strict=True), AfterValidator(validate_timestamp)]


def typed_id(prefix: str) -> Any:
    return Annotated[
        str,
        Field(strict=True, pattern=rf"^{prefix}_[0-9a-f]{{12}}4[0-9a-f]{{3}}[89ab][0-9a-f]{{15}}$"),
    ]


SessionId = typed_id("ses")
TurnId = typed_id("trn")
ArtifactId = typed_id("art")
JobId = typed_id("job")
BatchId = typed_id("bat")
ApprovalId = typed_id("apr")
PlanId = typed_id("pln")
ContextId = typed_id("ctx")
EventId = typed_id("evt")
WorkerId = typed_id("wrk")


def new_id(prefix: str) -> str:
    if prefix not in {"ses", "trn", "art", "job", "bat", "apr", "pln", "ctx", "evt", "wrk"}:
        raise ValueError("unknown ID prefix")
    return f"{prefix}_{uuid4().hex}"


def validate_json_numbers(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("JSON numbers must be finite")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            validate_json_numbers(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            validate_json_numbers(child)
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, allow_inf_nan=False, validate_assignment=True
    )

    @model_validator(mode="before")
    @classmethod
    def finite_json(cls, value: Any) -> Any:
        return validate_json_numbers(value)
