"""Offline production surface contracts, not live compatibility claims."""

from imagen_cli.cli.commands.profile import build_set
from imagen_cli.cli.registry import discover
from imagen_cli.providers.routes import SUPPORTED_ROUTES


def test_novelai_registered():
    assert ("novelai", "generate_image") in SUPPORTED_ROUTES


def test_profile_typed_options_schema_and_build():
    schema = discover("profile set")["input_schema"]
    assert "novelai_options" in schema["properties"]
    data = build_set(
        dict(
            profile="nai",
            provider="novelai",
            family="generate_image",
            base_url="https://example.test/prefix",
            model_default="manual-unverified",
            credential_ref="env:NAI_TEST",
            novelai_options={"auth_mode": "x-api-key", "reference_transport": "raw"},
        )
    )
    assert data["novelai_options"]["auth_mode"] == "x-api-key"


def test_catalog_requires_explicit_path_before_credentials():
    import pytest

    from imagen_cli.application.model_catalog_service import fetch

    with pytest.raises(ValueError, match="explicit"):
        fetch(
            {
                "provider": "novelai",
                "family": "generate_image",
                "base_url": "https://example.test/root",
                "models_path": None,
                "credential_ref": "env:NAI_TEST",
            },
            send_credential=True,
            resolve_credential=lambda _: pytest.fail("credential resolved"),
        )


def test_catalog_respects_selected_auth_mode():
    import httpx

    from imagen_cli.application.model_catalog_service import fetch

    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "manual-unverified"}]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        catalog = fetch(
            {
                "provider": "novelai",
                "family": "generate_image",
                "base_url": "https://example.test/root",
                "models_path": "/catalog",
                "credential_ref": "env:NAI_TEST",
                "novelai_options": {"auth_mode": "x-api-key"},
            },
            client=client,
            send_credential=True,
            resolve_credential=lambda _: "test-token",
        )
    assert requests[0].headers["x-api-key"] == "test-token"
    assert "authorization" not in requests[0].headers
    assert catalog["models"][0]["model_id"] == "manual-unverified"


def test_cli_profile_creation_and_preview_are_offline(tmp_path, monkeypatch, capsys):
    import json
    import socket

    from imagen_cli.application.container import Container
    from imagen_cli.cli.entrypoint import main
    from tests.support import capability

    monkeypatch.setattr(
        socket,
        "socket",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("preview must not use network")),
    )
    workspace = tmp_path / "workspace"
    profile = tmp_path / "nai.toml"
    profile.write_text(
        'id="nai"\nname="nai"\nprovider="novelai"\nfamily="generate_image"\n'
        'base_url="https://example.test/prefix"\nmodel_default="manual-unverified"\n'
        'credential_ref="env:UNRESOLVED_NAI"\nrevision=1\n[novelai_options]\nauth_mode="x-api-key"\n'
        'reference_transport="raw"\n[trust_policy]\nallowed_upload_targets=["https://example.test/prefix"]\n'
    )

    def cli(*args):
        code = main([*args, "--workspace", str(workspace), "--output", "json", "--no-interactive"])
        result = json.loads(capsys.readouterr().out)
        return code, result

    code, result = cli("profile", "add", "nai", "--config-file", str(profile))
    assert code == 0, result
    assert result["data"]["profile"]["novelai_options"]["auth_mode"] == "x-api-key"
    with Container(workspace=str(workspace)) as container:
        container.repositories.insert(
            "capabilities",
            capability(
                id="nai-generate",
                provider="novelai",
                family="generate_image",
                model="manual-unverified",
                parameter_schema={
                    "type": "object",
                    "properties": {
                        "count": {"const": 1},
                        "scale": {"type": "number"},
                        "sm": {"type": "boolean"},
                    },
                    "additionalProperties": False,
                },
            ),
        )
    cli("session", "create", "offline", "--profile", "nai", "--model", "manual-unverified")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("original prompt")
    code, result = cli("generate", "--session", "offline", "--prompt-file", str(prompt))
    assert code == 6, result
    assert result["data"]["plan"]["model"] == "manual-unverified"
