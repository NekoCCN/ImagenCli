import hashlib
from dataclasses import replace

import pytest

from imagen_cli.application.container import Container
from imagen_cli.application.novelai_vibe_service import VibePreparationService
from imagen_cli.application.profile_service import ProfileService
from imagen_cli.domain.primitive import new_id
from imagen_cli.providers.base import ReferenceInput
from imagen_cli.providers.novelai_references import map_references


class Encoder:
    def __init__(self):
        self.calls = []

    def encode(self, request):
        self.calls.append(request)
        return b"opaque-private-encoding"


def setup_vibe(container, png_bytes, encoder=None):
    profile = ProfileService().build(
        identity="vibe",
        provider="novelai",
        family="generate_image",
        base_url="https://example.test/prefix",
        model_default="nai-diffusion-4-5-full",
        credential_ref="env:UNRESOLVED_VIBE_KEY",
    )
    container.repositories.insert("profiles", profile.model_dump(mode="json"))
    identity = new_id("art")
    container.artifact_store.stage(identity, png_bytes)
    container.artifact_store.publish(identity)
    encoder = encoder or Encoder()
    service = VibePreparationService(
        container.repositories,
        container.artifact_store,
        container.private_state_store,
        adapter_factory=lambda _: encoder,
    )
    return service, profile, identity, encoder


def test_offline_plan_exact_approval_and_reuse(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        service, profile, identity, encoder = setup_vibe(container, png_bytes)
        plan = service.plan(profile.id, identity, profile.model_default, 0.6)
        assert not encoder.calls
        assert (
            plan["charge_disclosure"]
            == "Vibe preparation may incur a separate charge; amount unknown."
        )
        with pytest.raises(ValueError, match="digest"):
            service.execute(plan["id"], "0" * 64)
        assert not encoder.calls
        result = service.execute(plan["id"], plan["digest"])
        assert result["state"] == "succeeded"
        assert len(encoder.calls) == 1
        assert service.execute(plan["id"], plan["digest"]) == result
        reference = service.bind(
            profile,
            ReferenceInput(png_bytes, "vibe", {"information_extracted": 0.6}),
            profile.model_default,
        )
        assert reference.prepared_content == b"opaque-private-encoding"
        mapped = map_references(
            (reference,), mode="encoded", model=profile.model_default, require_approved=True
        )
        assert "reference_image_multiple" in mapped
        assert len(encoder.calls) == 1
        public = container.repositories.get("vibe_preparations", plan["id"])
        assert "private_relpath" not in public
        assert b"opaque-private-encoding" not in str(public).encode()


@pytest.mark.parametrize(
    "change", ["origin", "credential", "revision", "model", "source", "extraction", "auth"]
)
def test_cache_identity_is_exact(tmp_path, png_bytes, change):
    with Container(workspace=str(tmp_path)) as container:
        service, profile, identity, encoder = setup_vibe(container, png_bytes)
        plan = service.plan(profile.id, identity, profile.model_default, 0.6)
        service.execute(plan["id"], plan["digest"])
        sha = hashlib.sha256(png_bytes).hexdigest()
        model, extraction = profile.model_default, 0.6
        changes = {
            "origin": {"base_url": "https://other.test"},
            "credential": {"credential_ref": "env:OTHER"},
            "revision": {"revision": 2},
            "auth": {
                "novelai_options": {"auth_mode": "x-api-key", "reference_transport": "encoded"}
            },
        }
        if change in changes:
            profile = type(profile).model_validate(
                {**profile.model_dump(mode="json"), **changes[change]}
            )
        elif change == "model":
            model = "other-model"
        elif change == "source":
            sha = "0" * 64
        else:
            extraction = 0.7
        assert service.lookup(profile, sha, model, extraction) is None
        assert len(encoder.calls) == 1


def test_corrupt_cache_and_forged_binding_refused(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        service, profile, identity, _ = setup_vibe(container, png_bytes)
        plan = service.plan(profile.id, identity, profile.model_default, 1)
        service.execute(plan["id"], plan["digest"])
        reference = service.bind(
            profile, ReferenceInput(png_bytes, "vibe", {}), profile.model_default
        )
        with pytest.raises(ValueError):
            map_references(
                (replace(reference, prepared_content=b"tampered"),),
                mode="encoded",
                model=profile.model_default,
            )
        (container.private_state_store.root / (plan["id"] + ".bin")).write_bytes(b"corrupt")
        with pytest.raises(ValueError, match="hash"):
            service.lookup(profile, hashlib.sha256(png_bytes).hexdigest(), profile.model_default, 1)


@pytest.mark.parametrize("auth", ["bearer", "x-api-key"])
def test_encoder_official_json_binary_and_one_selected_auth(tmp_path, png_bytes, auth):
    import base64
    import json

    import httpx

    from imagen_cli.providers.novelai_vibe import NovelAIVibeAdapter, VibeEncodingRequest, identity

    with Container(workspace=str(tmp_path)) as container:
        _, profile, _, _ = setup_vibe(container, png_bytes)
        profile = type(profile).model_validate(
            {
                **profile.model_dump(mode="json"),
                "novelai_options": {"auth_mode": auth, "reference_transport": "encoded"},
            }
        )
        calls, credentials = [], []

        def handle(request):
            calls.append(request)
            assert str(request.url) == "https://example.test/prefix/ai/encode-vibe"
            assert json.loads(request.content) == {
                "image": base64.b64encode(png_bytes).decode(),
                "model": profile.model_default,
                "information_extracted": 0.5,
            }
            assert ("Authorization" in request.headers) is (auth == "bearer")
            assert ("X-API-Key" in request.headers) is (auth == "x-api-key")
            return httpx.Response(
                201, content=b"opaque", headers={"content-type": "application/binary"}
            )

        adapter = NovelAIVibeAdapter(
            profile,
            resolve_credential=lambda ref: credentials.append(ref) or "fixture",
            transport=httpx.MockTransport(handle),
        )
        key = identity(profile, hashlib.sha256(png_bytes).hexdigest(), profile.model_default, 0.5)
        request = VibeEncodingRequest(key, png_bytes)
        from imagen_cli.providers.base import ProviderFailure

        with pytest.raises(ProviderFailure):
            adapter.encode(request)
        assert not calls and not credentials
        assert (
            adapter.encode(replace(request, authorized=True, dispatch_intent_recorded=True))
            == b"opaque"
        )
        assert len(calls) == len(credentials) == 1


@pytest.mark.parametrize(
    "outcome", ["timeout", "redirect", "server", "wrong_type", "empty", "rate_limit"]
)
def test_encoder_never_retries_and_sanitizes_failures(tmp_path, png_bytes, outcome):
    import httpx

    from imagen_cli.providers.base import ProviderFailure
    from imagen_cli.providers.novelai_vibe import NovelAIVibeAdapter, VibeEncodingRequest, identity

    with Container(workspace=str(tmp_path)) as container:
        _, profile, _, _ = setup_vibe(container, png_bytes)
        calls = []

        def handle(request):
            calls.append(request)
            if outcome == "timeout":
                raise httpx.ReadTimeout("private provider body")
            if outcome == "redirect":
                return httpx.Response(
                    307,
                    headers={"location": "https://other.test"},
                    content=b"private provider body",
                )
            if outcome == "server":
                return httpx.Response(503, content=b"private provider body")
            if outcome == "rate_limit":
                return httpx.Response(429, content=b"private provider body")
            return httpx.Response(
                201,
                content=b"" if outcome == "empty" else b"private provider body",
                headers={
                    "content-type": "application/binary"
                    if outcome == "empty"
                    else "application/json"
                },
            )

        adapter = NovelAIVibeAdapter(
            profile, resolve_credential=lambda _: "fixture", transport=httpx.MockTransport(handle)
        )
        request = VibeEncodingRequest(
            identity(profile, hashlib.sha256(png_bytes).hexdigest(), profile.model_default, 1),
            png_bytes,
            True,
            True,
        )
        with pytest.raises(ProviderFailure) as caught:
            adapter.encode(request)
        assert len(calls) == 1
        assert "private provider body" not in str(caught.value)
        assert caught.value.accepted_maybe is (outcome != "rate_limit")


def test_plan_tampering_and_dispatch_profile_drift_refuse_before_encoder(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        service, profile, identity, encoder = setup_vibe(container, png_bytes)
        plan = service.plan(profile.id, identity, profile.model_default, 1)
        container.repositories.update("profiles", profile.id, {"credential_ref": "env:DIFFERENT"})
        with pytest.raises(ValueError, match="identity drifted"):
            service.execute(plan["id"], plan["digest"])
        assert not encoder.calls
        with pytest.raises(ValueError, match="immutable"):
            container.repositories.update("vibe_preparations", plan["id"], {"plan": {}})


def test_new_exact_preparation_plan_reuses_approved_cache_without_dispatch(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        service, profile, identity, encoder = setup_vibe(container, png_bytes)
        plan = service.plan(profile.id, identity, profile.model_default, 1)
        service.execute(plan["id"], plan["digest"])
        second = service.plan(profile.id, identity, profile.model_default, 1)
        assert second["plan"]["reuse"]["preparation_id"] == plan["id"]
        assert service.execute(second["id"], second["digest"])["state"] == "succeeded"
        assert len(encoder.calls) == 1


@pytest.mark.parametrize("drift", [None, "model", "credential", "bytes", "extraction"])
def test_generation_adapter_requires_matching_cache_identity_before_credentials(
    tmp_path, png_bytes, drift
):
    import base64
    import json

    import httpx

    from imagen_cli.providers.base import ProviderFailure
    from imagen_cli.providers.novelai import NovelAIAdapter
    from tests.contract.test_provider_adapters import request

    with Container(workspace=str(tmp_path)) as container:
        service, profile, artifact_id, encoder = setup_vibe(container, png_bytes)
        plan = service.plan(profile.id, artifact_id, profile.model_default, 1)
        service.execute(plan["id"], plan["digest"])
        reference = service.bind(
            profile, ReferenceInput(png_bytes, "vibe", {}), profile.model_default
        )
        approved = replace(request(profile), references=(reference,))
        if drift == "model":
            approved = replace(approved, model="different-model")
        elif drift == "credential":
            profile = profile.model_copy(update={"credential_ref": "env:DIFFERENT"})
        elif drift == "bytes":
            approved = replace(
                approved, references=(replace(reference, prepared_content=b"unverified"),)
            )
        elif drift == "extraction":
            approved = replace(
                approved, references=(replace(reference, settings={"information_extracted": 0.3}),)
            )
        calls, credentials = [], []

        def handle(req):
            calls.append(req)
            payload = json.loads(req.content)
            assert (
                base64.b64decode(payload["parameters"]["reference_image_multiple"][0])
                == b"opaque-private-encoding"
            )
            return httpx.Response(
                200, json={"images": [{"image": base64.b64encode(png_bytes).decode()}]}
            )

        adapter = NovelAIAdapter(
            profile,
            resolve_credential=lambda _: credentials.append(True) or "fixture",
            transport=httpx.MockTransport(handle),
        )
        if drift is None:
            assert adapter.create(approved).actual_count == 1
            assert len(calls) == len(credentials) == 1
        else:
            with pytest.raises(ProviderFailure, match="validation_error"):
                adapter.create(approved)
            assert not calls and not credentials
        assert len(encoder.calls) == 1


def test_expired_preparation_digest_never_dispatches(tmp_path, png_bytes):
    from datetime import UTC, datetime, timedelta

    with Container(workspace=str(tmp_path)) as container:
        service, profile, identity, encoder = setup_vibe(container, png_bytes)
        now = datetime.now(UTC)
        service.clock = lambda: now
        plan = service.plan(profile.id, identity, profile.model_default, 1)
        service.clock = lambda: now + timedelta(hours=25)
        with pytest.raises(ValueError, match="expired"):
            service.execute(plan["id"], plan["digest"])
        assert not encoder.calls
