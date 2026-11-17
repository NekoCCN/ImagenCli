import base64
import json
from dataclasses import replace
from decimal import Decimal

import httpx
import pytest

from imagen_cli.providers.base import ProviderFailure
from imagen_cli.providers.novelai import NovelAIAdapter
from imagen_cli.providers.novelai_parameters import normalize_parameters
from tests.contract.test_provider_adapters import profile, request


@pytest.mark.parametrize("auth", ["bearer", "x-api-key"])
@pytest.mark.parametrize("root", ["https://example.test/prefix", "https://example.test/prefix/"])
@pytest.mark.parametrize("operation", ["generate", "edit"])
def test_wire(auth, root, operation, png_bytes):
    target = profile("novelai", "generate_image", root, "custom-model")
    target = target.model_copy(
        update={
            "novelai_options": __import__(
                "imagen_cli.domain.models", fromlist=["NovelAIOptions"]
            ).NovelAIOptions(auth_mode=auth)
        }
    )
    calls = []

    def handler(req):
        calls.append(req)
        assert str(req.url) == "https://example.test/prefix/ai/generate-image"
        assert ("authorization" in req.headers) == (auth == "bearer")
        assert ("x-api-key" in req.headers) == (auth == "x-api-key")
        body = json.loads(req.content)
        assert body["input"] == "a red kite"
        assert body["model"] == "custom-model"
        assert body["action"] == ("img2img" if operation == "edit" else "generate")
        assert body["parameters"]["width"] == 64
        assert body["parameters"]["n_samples"] == 1
        assert body["parameters"]["negative_prompt"] == "bad"
        if operation == "edit":
            assert base64.b64decode(body["parameters"]["image"]) == png_bytes
        return httpx.Response(
            200, json={"images": [{"image": base64.b64encode(png_bytes).decode()}]}
        )

    result = NovelAIAdapter(
        target, resolve_credential=lambda _: "test-only", transport=httpx.MockTransport(handler)
    ).create(
        request(
            target,
            operation=operation,
            inputs=(png_bytes,) if operation == "edit" else (),
            parameters={
                "size": "64x64",
                "count": 1,
                "negative_prompt": "bad",
                **({"strength": 0.5, "noise": 0.1} if operation == "edit" else {}),
            },
        )
    )
    assert result.actual_count == 1
    assert len(calls) == 1


@pytest.mark.parametrize("status", [401, 403, 422, 429, 500, 302, 307, "timeout"])
def test_no_retry(status):
    target = profile("novelai", "generate_image", "https://example.test", "custom")
    calls = []

    def handler(req):
        calls.append(req)
        if status == "timeout":
            raise httpx.ReadTimeout("SECRET")
        return httpx.Response(status, text="SECRET", headers={"location": "https://evil.test"})

    with pytest.raises(ProviderFailure) as error:
        NovelAIAdapter(
            target, resolve_credential=lambda _: "test-only", transport=httpx.MockTransport(handler)
        ).create(request(target))
    assert len(calls) == 1
    assert "SECRET" not in str(error.value)
    assert error.value.retryable is False
    expected = {
        401: ("authentication_error", False),
        403: ("permission_denied", False),
        422: ("validation_error", False),
        429: ("rate_limited", False),
        500: ("provider_error", True),
        302: ("outcome_unknown", True),
        307: ("outcome_unknown", True),
        "timeout": ("outcome_unknown", True),
    }
    assert (error.value.type, error.value.accepted_maybe) == expected[status]
    assert error.value.http_status == (None if status == "timeout" else status)


@pytest.mark.parametrize(
    "change",
    [
        {"authorized": False},
        {"dispatch_intent_recorded": False},
        {"mask": b"x"},
        {"operation": "edit"},
        {"profile_revision": 2},
        {"native_context": object()},
    ],
)
def test_validation_before_credentials(change):
    target = profile("novelai", "generate_image", "https://example.test", "custom")

    def credential(_):
        pytest.fail("credential resolution before validation")

    with pytest.raises(ProviderFailure):
        NovelAIAdapter(target, resolve_credential=credential).create(
            replace(request(target), **change)
        )


def test_decimal_normalized():
    result = normalize_parameters({"scale": Decimal("5.5")}, model="custom", operation="generate")
    assert json.loads(json.dumps(result))["scale"] == 5.5


@pytest.mark.parametrize("root", ["http://example.test", "http://127.0.0.1.evil.test"])
def test_nonlocal_http_rejected(root):
    target = profile("novelai", "generate_image", root, "custom")

    def credential(_):
        pytest.fail("insecure credential resolution")

    with pytest.raises(ProviderFailure):
        NovelAIAdapter(target, resolve_credential=credential).create(request(target))


def test_rate_limit_public_vocabulary():
    from imagen_cli.domain.contracts import MachineError
    from imagen_cli.domain.enums import ErrorType
    from tests.contract.test_machine_contracts import validator

    assert ErrorType("rate_limited").value == "rate_limited"
    error = MachineError(
        type="rate_limited",
        message="rate_limited",
        retryable=False,
        accepted_maybe=False,
        next_actions=[],
    )
    validator("result.schema.json").validate(
        {
            "schema_version": 1,
            "ok": False,
            "status": "failed",
            "data": {},
            "error": error.model_dump(mode="json"),
        }
    )
