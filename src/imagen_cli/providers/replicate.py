"""Replicate predictions transport.

Vendor-neutral by construction: the caller names a model and inputs, this module
owns submission, polling, download and error mapping. No credentials are stored
or logged; every string that reaches an exception passes through :func:`_scrub`.
"""

from __future__ import annotations

import base64
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

__all__ = [
    "REPLICATE_ENDPOINT",
    "Prediction",
    "ReplicateError",
    "data_uri",
    "download",
    "predict",
]

REPLICATE_ENDPOINT = "https://api.replicate.com"

#: Terminal statuses; anything else is polled.
_TERMINAL = frozenset({"succeeded", "failed", "canceled"})
_PENDING = frozenset({"starting", "processing"})

# Bounded on purpose: an unbounded tail lets a crafted error body backtrack.
#: How many throttled submissions to retry before giving up.
_RATE_LIMIT_RETRIES = 5

_SECRET = re.compile(r"(?:r8_|sk-)[A-Za-z0-9_-]{1,256}")


def _scrub(text: str) -> str:
    return _SECRET.sub("[redacted]", text)


class ReplicateError(Exception):
    """A predictions failure with a machine-readable kind.

    ``kind`` is one of ``authentication_error``, ``insufficient_credit``,
    ``rate_limited``, ``timeout``, ``canceled``, ``failed``.
    """

    def __init__(self, kind: str, detail: str, *, retriable: bool = False,
                 retry_after: float | None = None) -> None:
        self.kind = kind
        self.detail = _scrub(detail)
        self.retriable = retriable
        self.retry_after = retry_after
        super().__init__(f"{kind}: {self.detail}")


@dataclass(frozen=True)
class Prediction:
    id: str
    status: str
    output: tuple[str, ...]
    error: str | None = None
    metrics: Mapping[str, Any] = field(default_factory=dict)


def data_uri(payload: bytes, media_type: str = "image/png") -> str:
    """Inline a file as a data URI; Replicate accepts these for file inputs."""
    return f"data:{media_type};base64,{base64.b64encode(payload).decode('ascii')}"


def _normalize_output(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if item is not None)
    return (str(value),)


def _retry_after(response: httpx.Response) -> float | None:
    header = response.headers.get("retry-after")
    if header is None:
        return None
    try:
        return max(0.0, float(header))
    except ValueError:
        return None


def _map_status(code: int, body: str, response: httpx.Response | None = None) -> ReplicateError:
    detail = _scrub(body)[:400] or f"HTTP {code}"
    if code in (401, 403):
        return ReplicateError("authentication_error", detail)
    if code == 402:
        return ReplicateError("insufficient_credit", detail)
    if code == 429:
        return ReplicateError("rate_limited", detail, retriable=True,
                              retry_after=_retry_after(response) if response else None)
    return ReplicateError("failed", detail, retriable=code >= 500)


def _client(token: str, transport: httpx.BaseTransport | None) -> httpx.Client:
    return httpx.Client(
        base_url=REPLICATE_ENDPOINT,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        timeout=60.0,
        transport=transport,
    )


def _submit(client: httpx.Client, model: str, version: str | None,
            inputs: Mapping[str, Any]) -> dict[str, Any]:
    if version:
        response = client.post("/v1/predictions", json={"version": version,
                                                        "input": dict(inputs)})
    else:
        response = client.post(f"/v1/models/{model}/predictions",
                               json={"input": dict(inputs)})
    if response.status_code >= 400:
        raise _map_status(response.status_code, response.text, response)
    return dict(response.json())


def _to_prediction(payload: Mapping[str, Any]) -> Prediction:
    return Prediction(
        id=str(payload.get("id") or ""),
        status=str(payload.get("status") or ""),
        output=_normalize_output(payload.get("output")),
        error=None if payload.get("error") is None else _scrub(str(payload["error"])),
        metrics=dict(payload.get("metrics") or {}),
    )


def predict(
    model: str,
    inputs: Mapping[str, Any],
    *,
    token: str,
    version: str | None = None,
    transport: httpx.BaseTransport | None = None,
    poll_interval: float = 1.0,
    poll_max: float = 5.0,
    timeout: float = 300.0,
    sleep: Callable[[float], None] = time.sleep,
) -> Prediction:
    """Submit a prediction and block until it reaches a terminal state.

    ``timeout`` bounds the total time spent waiting, not a single HTTP request.
    """
    with _client(token, transport) as client:
        # New accounts are throttled hard (6 predictions/minute), so a single 429
        # must not end the request: wait out Retry-After inside the same budget.
        waited = 0.0
        attempts = 0
        while True:
            try:
                submitted = _to_prediction(_submit(client, model, version, inputs))
                break
            except ReplicateError as error:
                if error.kind != "rate_limited" or attempts >= _RATE_LIMIT_RETRIES:
                    raise
                delay = error.retry_after if error.retry_after is not None else poll_max
                if waited + delay > timeout:
                    raise
                sleep(delay)
                waited += delay
                attempts += 1
        if not submitted.id:
            raise ReplicateError("failed", "prediction has no id")
        if submitted.status in _TERMINAL:
            return _finish(submitted)

        interval = poll_interval
        while True:
            if waited + interval > timeout:
                raise ReplicateError(
                    "timeout",
                    f"prediction {submitted.id} did not finish within {timeout:.0f}s",
                )
            sleep(interval)
            waited += interval
            response = client.get(f"/v1/predictions/{submitted.id}")
            if response.status_code >= 400:
                raise _map_status(response.status_code, response.text)
            current = _to_prediction(response.json())
            if current.status in _TERMINAL:
                return _finish(current)
            if current.status not in _PENDING:
                raise ReplicateError(
                    "failed", f"prediction {submitted.id} reported {current.status!r}"
                )
            interval = min(interval * 1.5, poll_max)


def _finish(prediction: Prediction) -> Prediction:
    if prediction.status == "succeeded":
        if not prediction.output:
            raise ReplicateError("failed", f"prediction {prediction.id} returned no output")
        return prediction
    if prediction.status == "canceled":
        raise ReplicateError("canceled", f"prediction {prediction.id} was canceled")
    raise ReplicateError(
        "failed", prediction.error or f"prediction {prediction.id} failed"
    )


def download(url: str, *, token: str,
             transport: httpx.BaseTransport | None = None) -> bytes:
    """Fetch a delivery URL. The token is harmless here and keeps private URLs working."""
    with _client(token, transport) as client:
        response = client.get(url)
    if response.status_code >= 400:
        raise _map_status(response.status_code, response.text)
    return response.content
