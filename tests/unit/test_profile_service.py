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
