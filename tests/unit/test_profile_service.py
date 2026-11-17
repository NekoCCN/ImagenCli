from __future__ import annotations

import pytest
from pydantic import ValidationError

from imagen_cli.application.profile_service import ProfileService


def profile_data(**changes):
    value = {
        "id": "openai-local",
        "name": "OpenAI local",
        "provider": "openai",
        "family": "images",
        "base_url": "https://api.example.test/v1",
        "model_default": "image-model",
        "credential_ref": "keyring:openai",
        "trust_policy": {"allow_redirects": False},
        "revision": 1,
    }
    value.update(changes)
    return value


def test_load_profile_preserves_explicit_endpoint_and_only_credential_ref():
    service = ProfileService()
    profile = service.load(profile_data())

    assert profile.base_url == "https://api.example.test/v1"
    assert profile.credential_ref == "keyring:openai"
    assert profile.revision == 1
    assert "api_key" not in profile.model_dump()


def test_profile_rejects_plaintext_secret_and_implicit_proxy_settings():
    service = ProfileService()
    with pytest.raises(ValueError, match="credential_ref"):
        service.load(profile_data(api_key="not-a-secret-storage-reference"))
    with pytest.raises(ValueError, match="proxy"):
        service.load(profile_data(proxy="http://proxy.example.test"))


def test_profile_revision_is_explicit_and_positive():
    with pytest.raises(ValidationError):
        ProfileService().load(profile_data(revision=0))
    with pytest.raises(ValidationError):
        ProfileService().load(profile_data(revision="1"))


def test_old_profile_serialized_shape_unchanged():
    profile = ProfileService().load(profile_data())
    assert "novelai_options" not in profile.model_dump()
    assert 'novelai_options' not in profile.model_dump_json()
    assert ProfileService().load(profile.model_dump()).model_dump() == profile.model_dump()


def test_novelai_options_roundtrip_and_build():
    options = {"auth_mode": "x-api-key", "reference_transport": "raw"}
    service = ProfileService()
    profile = service.load(profile_data(novelai_options=options))
    assert profile.model_dump()["novelai_options"] == options
    assert service.load(profile.model_dump()).novelai_options == profile.novelai_options
    built = service.build(identity="nai", provider="novelai", family="generate_image",
                          base_url="https://example.test", model_default="custom",
                          credential_ref="env:NAI_KEY", novelai_options=options)
    assert built.novelai_options == profile.novelai_options


@pytest.mark.parametrize("options", [
    {"auth_mode": "basic"}, {"reference_transport": "automatic"}, {"headers": {}},
])
def test_novelai_options_are_strict(options):
    with pytest.raises(ValidationError):
        ProfileService().load(profile_data(novelai_options=options))


def test_novelai_options_survive_repository_reopen(tmp_path):
    from imagen_cli.application.container import Container

    workspace = str(tmp_path / "work")
    service = ProfileService()
    profile = service.load(profile_data(novelai_options={}))
    assert profile.novelai_options.auth_mode == "bearer"
    assert profile.novelai_options.reference_transport == "encoded"
    with Container(workspace=workspace) as container:
        container.repositories.insert("profiles", profile.model_dump())
    with Container(workspace=workspace) as container:
        stored = container.repositories.get("profiles", profile.id)
    assert service.load(stored).model_dump() == profile.model_dump()
