"""Configured dispatch reaches the Gemini adapter without touching the network."""

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
