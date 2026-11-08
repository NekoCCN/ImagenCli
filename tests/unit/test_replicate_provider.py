"""Replicate predictions transport: submit, poll, download, and error mapping."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from imagen_cli.providers import replicate

Handler = Callable[[httpx.Request], httpx.Response]
Route = httpx.Response | Handler


def _body(request: httpx.Request) -> dict[str, Any]:
    return dict(json.loads(request.content))


def _router(routes: dict[tuple[str, str], Route]) -> httpx.MockTransport:
    """Dispatch on (method, url suffix); tests register only what they expect."""

    def handler(request: httpx.Request) -> httpx.Response:
        for (method, suffix), route in routes.items():
            if request.method == method and str(request.url).endswith(suffix):
                return route(request) if callable(route) else route
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    return httpx.MockTransport(handler)


def test_submit_uses_model_endpoint_and_data_uri():
    seen: dict[str, Any] = {}

    def submitted(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = _body(request)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(201, json={"id": "p1", "status": "starting"})

    routes: dict[tuple[str, str], Route] = {
        ("POST", "/v1/models/sprited/birefnet-toonout/predictions"): submitted,
        ("GET", "/v1/predictions/p1"): httpx.Response(
            200, json={"id": "p1", "status": "succeeded", "output": ["https://x/out.png"]}
        ),
    }
    result = replicate.predict(
        "sprited/birefnet-toonout",
        {"image": replicate.data_uri(b"\x89PNG\r\n")},
        token="r8_token",
        transport=_router(routes),
        sleep=lambda _seconds: None,
    )

    assert seen["url"] == (
        "https://api.replicate.com/v1/models/sprited/birefnet-toonout/predictions"
    )
    assert seen["body"]["input"]["image"].startswith("data:image/png;base64,")
    assert "version" not in seen["body"]
    assert seen["auth"] == "Bearer r8_token"
    assert result.status == "succeeded"
    assert result.output == ("https://x/out.png",)


def test_pinned_version_uses_predictions_endpoint():
    seen: dict[str, Any] = {}

    def submitted(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = _body(request)
        return httpx.Response(201, json={"id": "p2", "status": "starting"})

    routes: dict[tuple[str, str], Route] = {
        ("POST", "/v1/predictions"): submitted,
        ("GET", "/v1/predictions/p2"): httpx.Response(
            200, json={"id": "p2", "status": "succeeded", "output": "https://x/o.png"}
        ),
    }
    result = replicate.predict(
        "sprited/birefnet", {"image": "data:image/png;base64,AA"},
        token="t", version="v1", transport=_router(routes), sleep=lambda _s: None,
    )

    assert seen["url"] == "https://api.replicate.com/v1/predictions"
    assert seen["body"]["version"] == "v1"
    assert result.output == ("https://x/o.png",)


def test_polls_until_terminal():
    polls = {"count": 0}

    def polled(_request: httpx.Request) -> httpx.Response:
        polls["count"] += 1
        if polls["count"] < 3:
            return httpx.Response(200, json={"id": "p3", "status": "processing"})
        return httpx.Response(
            200, json={"id": "p3", "status": "succeeded", "output": ["https://x/o.png"]}
        )

    routes: dict[tuple[str, str], Route] = {
        ("POST", "/v1/models/o/m/predictions"): httpx.Response(
            201, json={"id": "p3", "status": "starting"}),
        ("GET", "/v1/predictions/p3"): polled,
    }
    result = replicate.predict(
        "o/m", {"image": "data:image/png;base64,AA"}, token="t",
        transport=_router(routes), sleep=lambda _s: None,
    )
    assert polls["count"] == 3
    assert result.output == ("https://x/o.png",)


def test_failed_prediction_raises_with_detail():
    routes: dict[tuple[str, str], Route] = {
        ("POST", "/v1/models/o/m/predictions"): httpx.Response(
            201, json={"id": "p4", "status": "starting"}),
        ("GET", "/v1/predictions/p4"): httpx.Response(
            200, json={"id": "p4", "status": "failed", "error": "CUDA out of memory"}
        ),
    }
    with pytest.raises(replicate.ReplicateError) as caught:
        replicate.predict(
            "o/m", {}, token="t", transport=_router(routes), sleep=lambda _s: None,
        )
    assert caught.value.kind == "failed"
    assert "CUDA out of memory" in caught.value.detail


def test_canceled_prediction_raises():
    routes: dict[tuple[str, str], Route] = {
        ("POST", "/v1/models/o/m/predictions"): httpx.Response(
            201, json={"id": "p5", "status": "starting"}),
        ("GET", "/v1/predictions/p5"): httpx.Response(
            200, json={"id": "p5", "status": "canceled"}
        ),
    }
    with pytest.raises(replicate.ReplicateError) as caught:
        replicate.predict(
            "o/m", {}, token="t", transport=_router(routes), sleep=lambda _s: None,
        )
    assert caught.value.kind == "canceled"


def test_timeout_stops_polling():
    routes: dict[tuple[str, str], Route] = {
        ("POST", "/v1/models/o/m/predictions"): httpx.Response(
            201, json={"id": "p6", "status": "starting"}),
        ("GET", "/v1/predictions/p6"): httpx.Response(
            200, json={"id": "p6", "status": "processing"}),
    }
    with pytest.raises(replicate.ReplicateError) as caught:
        replicate.predict(
            "o/m", {}, token="t", transport=_router(routes),
            poll_interval=1.0, poll_max=1.0, timeout=3.0, sleep=lambda _s: None,
        )
    assert caught.value.kind == "timeout"


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "authentication_error"), (403, "authentication_error"),
     (402, "insufficient_credit"), (429, "rate_limited")],
)
def test_submit_status_codes_map_to_kinds(status: int, kind: str):
    routes: dict[tuple[str, str], Route] = {
        ("POST", "/v1/models/o/m/predictions"): httpx.Response(status, json={}),
    }
    with pytest.raises(replicate.ReplicateError) as caught:
        replicate.predict(
            "o/m", {}, token="t", transport=_router(routes), sleep=lambda _s: None,
        )
    assert caught.value.kind == kind


def test_download_sends_token_and_returns_bytes():
    seen: dict[str, Any] = {}

    def served(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, content=b"\x89PNG-payload")

    result = replicate.download(
        "https://delivery/out.png", token="t", transport=httpx.MockTransport(served)
    )
    assert result == b"\x89PNG-payload"
    assert seen["auth"] == "Bearer t"


def test_error_text_scrubs_credentials():
    fake_token = "r8_" + "x" * 37
    routes: dict[tuple[str, str], Route] = {
        ("POST", "/v1/models/o/m/predictions"): httpx.Response(
            401, json={"detail": f"bad token {fake_token}"}
        ),
    }
    with pytest.raises(replicate.ReplicateError) as caught:
        replicate.predict(
            "o/m", {}, token="t", transport=_router(routes), sleep=lambda _s: None,
        )
    assert fake_token not in caught.value.detail
    assert "[redacted]" in caught.value.detail

def test_data_uri_round_trips_payload():
    uri = replicate.data_uri(b"abc", media_type="image/webp")
    assert uri == "data:image/webp;base64,YWJj"
