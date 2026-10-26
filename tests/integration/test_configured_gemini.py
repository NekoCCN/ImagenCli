"""Configured dispatch reaches the Gemini adapter without touching the network."""

import base64
import json
from dataclasses import replace

import httpx
import pytest

from imagen_cli.application.container import Container
from imagen_cli.domain.primitive import new_id
from imagen_cli.execution.configured import ConfiguredAdapter
from imagen_cli.providers.base import ArtifactDescriptor, ImmediateResult, ValidatedRequest

PROFILE = {
    "id": "gemini", "name": "Gemini", "provider": "gemini",
    "family": "generate_content",
    "base_url": "https://generativelanguage.googleapis.com/v1beta",
    "model_default": "gemini-2.5-flash-image", "credential_ref": "env:IMAGEN_GEMINI_KEY",
    "trust_policy": {}, "revision": 1,
}


def _request(profile="gemini"):
    return ValidatedRequest(
        "pln_" + "1" * 32, "job_" + "2" * 32, profile, 1, "generate",
        "gemini-2.5-flash-image", "2026-10", "red kite", {"count": 1},
        "a" * 64, True, True,
    )


def test_configured_adapter_dispatches_gemini(tmp_path, monkeypatch):
    with Container(workspace=str(tmp_path)) as container:
        container.repositories.insert("profiles", PROFILE)
        calls = []

        def create(self, request):
            calls.append(request)
            return ImmediateResult(
                1, 1, (ArtifactDescriptor(new_id("art"), 0, b"image"),),
            )

        monkeypatch.setattr(
            "imagen_cli.execution.configured.GeminiAdapter.create", create,
        )
        adapter = ConfiguredAdapter(
            container.repositories, private_store=container.private_state_store,
        )
        result = adapter.create(_request())
        assert result.actual_count == 1
        assert len(calls) == 1
        assert calls[0].model == "gemini-2.5-flash-image"


def test_unknown_family_is_refused(tmp_path):
    from imagen_cli.providers.base import ProviderFailure

    with Container(workspace=str(tmp_path)) as container:
        container.repositories.insert(
            "profiles", {**PROFILE, "family": "chat", "id": "other"},
        )
        adapter = ConfiguredAdapter(
            container.repositories, private_store=container.private_state_store,
        )
        with pytest.raises(ProviderFailure):
            adapter.create(_request("other"))


def test_configured_native_roundtrip_uses_fresh_adapter(tmp_path, monkeypatch, png_bytes):
    encoded = base64.b64encode(png_bytes).decode()
    native = {"role": "model", "parts": [{
        "inlineData": {"mimeType": "image/png", "data": encoded},
        "thoughtSignature": "fixture-private-signature",
    }]}
    sent_contents = []

    def send(sent):
        assert str(sent.url) == (
            "https://gateway.example/gemini/v1beta/models/nano-banana-2:generateContent"
        )
        assert sent.headers["x-goog-api-key"] == "fixture-key"
        sent_contents.append(json.loads(sent.content)["contents"])
        return httpx.Response(200, json={
            "candidates": [{"content": native, "finishReason": "STOP"}],
        })

    monkeypatch.setattr(
        "imagen_cli.providers.gemini.httpx.HTTPTransport",
        lambda **kwargs: httpx.MockTransport(send),
    )
    monkeypatch.setattr(
        "imagen_cli.execution.configured.resolve_credential", lambda *args, **kwargs: "fixture-key",
    )
    with Container(workspace=str(tmp_path)) as container:
        container.repositories.insert("profiles", {
            **PROFILE, "base_url": "https://gateway.example/gemini/v1beta",
            "model_default": "nano-banana-2",
        })
        request = replace(_request(), model="nano-banana-2")
        first = ConfiguredAdapter(
            container.repositories, private_store=container.private_state_store,
        ).create(request)
        second = ConfiguredAdapter(
            container.repositories, private_store=container.private_state_store,
        ).create(replace(
            request, job_id="job_" + "3" * 32, operation="edit", prompt="make blue",
            native_context=first.context,
        ))
        assert second.artifacts[0].content == png_bytes
        assert second.context != first.context
        assert sent_contents == [
            [{"role": "user", "parts": [{"text": "red kite"}]}],
            [{"role": "user", "parts": [{"text": "red kite"}]}, native,
             {"role": "user", "parts": [{"text": "make blue"}]}],
        ]
        assert "fixture-private-signature" not in repr(second)
