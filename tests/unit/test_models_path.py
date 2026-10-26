"""Catalog routing is explicit, same-origin and independent of generation protocol."""

import httpx
import pytest

from imagen_cli.application.container import Container
from imagen_cli.application.model_catalog_service import fetch
from imagen_cli.application.profile_service import ProfileService


def parameters(**extra):
    return {
        "profile": "gateway",
        "provider": "gemini",
        "family": "generate_content",
        "base_url": "https://gateway.example/v1beta",
        "model_default": "nano-banana-2",
        "credential_ref": "env:KEY",
        **extra,
    }


def test_cli_profile_persists_default_and_custom_models_path(tmp_path):
    with Container(workspace=str(tmp_path)) as container:
        status, data = container.execute("profile set", parameters())
        assert status == "succeeded"
        assert data["profile"]["models_path"] == "/v1/models"
        status, data = container.execute("profile set", parameters(models_path="/custom/models"))
        assert status == "succeeded"
        assert data["profile"]["models_path"] == "/custom/models"
        assert container.repositories.get("profiles", "gateway")["models_path"] == "/custom/models"


def test_gemini_catalog_path_uses_origin_and_openai_schema_fallback():
    urls = []

    def handler(request):
        urls.append(str(request.url))
        assert request.headers["x-goog-api-key"] == "test-secret"
        return httpx.Response(200, json={"data": [{"id": "nano-banana-2"}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = fetch(
            parameters(models_path="/v1/models"),
            client=client,
            send_credential=True,
            resolve_credential=lambda _: "test-secret",
        )
    assert urls == ["https://gateway.example/v1/models"]
    assert result["models"] == [{"model_id": "nano-banana-2", "display_name": "nano-banana-2"}]


def test_native_empty_models_does_not_fall_back_to_data():
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"models": [], "data": [{"id": "wrong"}]})
        )
    ) as client:
        assert fetch(parameters(models_path="/v1/models"), client=client)["models"] == []


@pytest.mark.parametrize(
    "path",
    [
        "https://evil.example/models",
        "//evil.example/models",
        "/v1/models?key=x",
        "/v1/models#x",
        "/../models",
        "/%2e%2e/models",
        "/v1\\models",
        "models",
        "",
    ],
)
def test_invalid_catalog_path_rejected_before_credentials(path):
    def forbidden(_):
        pytest.fail("invalid path must not resolve credentials or send HTTP")

    with pytest.raises(ValueError, match="models_path"):
        fetch(parameters(models_path=path), send_credential=True, resolve_credential=forbidden)
    with pytest.raises(ValueError, match="models_path"):
        ProfileService().build(
            identity="gateway",
            **{k: v for k, v in parameters(models_path=path).items() if k != "profile"},
        )


def test_path_change_invalidates_catalog_and_omitted_path_preserves_it(tmp_path):
    with Container(workspace=str(tmp_path)) as container:
        container.execute("profile set", parameters())
        container.execute("models add", {"profile": "gateway", "id": "old"})
        container.execute("profile set", parameters(models_path="/v1beta/models"))
        assert container.repositories.list("provider_models") == []
        status, data = container.execute("profile set", parameters(revision=2))
        assert status == "succeeded"
        assert data["profile"]["models_path"] == "/v1beta/models"


def test_legacy_catalog_keeps_base_path():
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"models": []}))
    ) as client:
        assert (
            fetch(parameters(), client=client)["source_url"]
            == "https://gateway.example/v1beta/models"
        )
