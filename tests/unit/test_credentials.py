"""Credential references: env and optional keyring, never inlined."""

import sys
from types import SimpleNamespace

import pytest

from imagen_cli.application.profile_service import ProfileService
from imagen_cli.execution.configured import resolve_credential


def test_env_reference(monkeypatch):
    monkeypatch.setenv("DEMO_KEY", "secret")
    assert resolve_credential("env:DEMO_KEY") == "secret"
    with pytest.raises(ValueError, match="unavailable"):
        resolve_credential("env:MISSING_KEY")


def test_keyring_reference(monkeypatch):
    calls = []

    def get_password(service, username):
        calls.append((service, username))
        return "keyring-secret"

    monkeypatch.setitem(sys.modules, "keyring", SimpleNamespace(get_password=get_password))
    assert resolve_credential("keyring:imagen-demo") == "keyring-secret"
    assert calls == [("imagen-demo", "imagen")]


def test_missing_keyring_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "keyring", None)
    with pytest.raises(ValueError, match="backend unavailable"):
        resolve_credential("keyring:imagen-demo")


def test_unsupported_and_unsafe_references(monkeypatch):
    with pytest.raises(ValueError, match="unsupported"):
        resolve_credential("file:/etc/passwd")
    monkeypatch.setenv("BAD_KEY", "line\nbreak")
    with pytest.raises(ValueError, match="unavailable"):
        resolve_credential("env:BAD_KEY")


def test_profile_build_accepts_keyring():
    profile = ProfileService().build(
        identity="p", provider="openai", family="images",
        base_url="https://gateway.example/v1", model_default="m",
        credential_ref="keyring:imagen-demo",
    )
    assert profile.credential_ref == "keyring:imagen-demo"
    with pytest.raises(ValueError, match="env:NAME"):
        ProfileService().build(
            identity="p", provider="openai", family="images",
            base_url="https://gateway.example/v1", model_default="m",
            credential_ref="plaintext",
        )


def test_models_refresh_resolves_private_credentials(tmp_path, monkeypatch):
    from imagen_cli.application import model_catalog_service
    from imagen_cli.application.container import Container

    seen = []

    def fake_refresh(repositories, profile, *, resolve_credential, send_credential):
        seen.append(resolve_credential(profile["credential_ref"]))
        return {"profile": profile["id"], "models": []}

    monkeypatch.setattr(model_catalog_service, "refresh", fake_refresh)

    with Container(workspace=str(tmp_path / "ws")) as container:
        reference = container.private_state_store.write_credential("demo", "sk-private")
        container.repositories.insert("profiles", {
            "id": "demo", "name": "demo", "provider": "openai", "family": "images",
            "base_url": "https://gateway.example/v1", "model_default": "m",
            "credential_ref": reference, "trust_policy": {}, "revision": 1,
        })
        status, _ = container.execute(
            "models refresh", {"profile": "demo", "send_credential": True}
        )
    assert status == "succeeded"
    assert seen == ["sk-private"]

    from imagen_cli.application.container import Container
    from imagen_cli.execution.configured import resolve_credential

    with Container(workspace=str(tmp_path / "ws")) as container:
        store = container.private_state_store
        reference = store.write_credential("demo", "sk-private")
        assert reference.startswith("private:")
        assert resolve_credential(reference, private_store=store) == "sk-private"
        again = store.write_credential("demo", "sk-rotated")
        assert again == reference
        assert resolve_credential(reference, private_store=store) == "sk-rotated"
        with pytest.raises(ValueError, match="backend unavailable"):
            resolve_credential(reference)
        with pytest.raises(ValueError, match="unavailable"):
            resolve_credential("private:missing", private_store=container.private_state_store)
