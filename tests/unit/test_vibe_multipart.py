import hashlib
from dataclasses import replace
from email.parser import BytesParser
from email.policy import default

import httpx
import pytest
from test_novelai_vibe_cache import setup_vibe

from imagen_cli.application.container import Container
from imagen_cli.domain.models import NovelAIOptions
from imagen_cli.providers.base import ProviderFailure
from imagen_cli.providers.novelai_vibe import NovelAIVibeAdapter, VibeEncodingRequest, identity


@pytest.mark.parametrize("auth", ["bearer", "x-api-key"])
@pytest.mark.parametrize("transport", ["json", "multipart"])
def test_encoding_wire(tmp_path, png_bytes, auth, transport):
    with Container(workspace=str(tmp_path)) as container:
        _, profile, _, _ = setup_vibe(container, png_bytes)
        profile.novelai_options = NovelAIOptions(auth_mode=auth, vibe_encoding_transport=transport)
        key = identity(profile, hashlib.sha256(png_bytes).hexdigest(), profile.model_default, 0.6)
        calls = []

        def handle(request):
            calls.append(request)
            assert request.headers["Authorization" if auth == "bearer" else "X-API-Key"] == (
                "Bearer synthetic" if auth == "bearer" else "synthetic")
            if transport == "multipart":
                message = BytesParser(policy=default).parsebytes(
                    b"Content-Type: " + request.headers["content-type"].encode()
                    + b"\r\n\r\n" + request.content)
                parts = {p.get_param("name", header="content-disposition"): p
                         for p in message.iter_parts()}
                assert set(parts) == {"image", "model", "information_extracted"}
                assert parts["image"].get_payload(decode=True) == png_bytes
                assert parts["image"].get_content_type() == "image/png"
                assert parts["model"].get_payload(decode=True).decode() == profile.model_default
                assert parts["information_extracted"].get_payload(decode=True) == b"0.6"
            else:
                import base64
                import json
                assert json.loads(request.content) == {
                    "image": base64.b64encode(png_bytes).decode(),
                    "model": profile.model_default, "information_extracted": 0.6}
            return httpx.Response(
                201, headers={"Content-Type": "application/binary"}, content=b"encoded")

        adapter = NovelAIVibeAdapter(profile, resolve_credential=lambda _: "synthetic",
                                    transport=httpx.MockTransport(handle))
        request = VibeEncodingRequest(key, png_bytes, True, True)
        with pytest.raises(ProviderFailure):
            adapter.encode(replace(request, authorized=False))
        assert not calls
        assert adapter.encode(request) == b"encoded"
        assert len(calls) == 1


def test_default_and_transport_identity(tmp_path, png_bytes):
    assert NovelAIOptions().vibe_encoding_transport == "json"
    with Container(workspace=str(tmp_path)) as container:
        service, profile, artifact, encoder = setup_vibe(container, png_bytes)
        plan = service.plan(profile.id, artifact, profile.model_default, 0.6)
        profile.novelai_options = NovelAIOptions(vibe_encoding_transport="multipart")
        container.repositories.update("profiles", profile.id, {
            "novelai_options": profile.novelai_options.model_dump(mode="json")})
        with pytest.raises(ValueError):
            service.execute(plan["id"], plan["digest"])
        assert not encoder.calls
        assert service.lookup(
            profile, hashlib.sha256(png_bytes).hexdigest(), profile.model_default, 0.6) is None


def test_schema_and_legacy_default_serialization():
    from imagen_cli.cli.registry import discover

    assert NovelAIOptions().model_dump(mode="json") == {
        "auth_mode": "bearer", "reference_transport": "encoded"}
    for command in ("profile set",):
        options = discover(command)["input_schema"]["properties"]["novelai_options"]
        assert options["properties"]["vibe_encoding_transport"] == {
            "enum": ["json", "multipart"], "default": "json"}
    with pytest.raises(ValueError):
        NovelAIOptions(vibe_encoding_transport="auto")


def test_successful_json_cache_is_not_reused_by_multipart(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        service, profile, artifact, encoder = setup_vibe(container, png_bytes)
        plan = service.plan(profile.id, artifact, profile.model_default, 0.6)
        service.execute(plan["id"], plan["digest"])
        assert len(encoder.calls) == 1
        profile.novelai_options = NovelAIOptions(vibe_encoding_transport="multipart")
        container.repositories.update("profiles", profile.id, {
            "novelai_options": profile.novelai_options.model_dump(mode="json")})
        fresh = service.plan(profile.id, artifact, profile.model_default, 0.6)
        assert fresh["digest"] != plan["digest"]
        assert "reuse" not in fresh
        assert len(encoder.calls) == 1


def test_multipart_drift_invalid_image_and_no_fallback(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        _, profile, _, _ = setup_vibe(container, png_bytes)
        old = identity(profile, hashlib.sha256(png_bytes).hexdigest(), profile.model_default, 1)
        profile.novelai_options = NovelAIOptions(vibe_encoding_transport="multipart")
        calls = []

        def handle(request):
            calls.append(request)
            return httpx.Response(415)

        adapter = NovelAIVibeAdapter(profile, resolve_credential=lambda _: "synthetic",
                                    transport=httpx.MockTransport(handle))
        with pytest.raises(ProviderFailure):
            adapter.encode(VibeEncodingRequest(old, png_bytes, True, True))
        current = identity(profile, hashlib.sha256(png_bytes).hexdigest(), profile.model_default, 1)
        with pytest.raises(ProviderFailure):
            adapter.encode(VibeEncodingRequest(current, b"not an image", True, True))
        assert not calls
        with pytest.raises(ProviderFailure):
            adapter.encode(VibeEncodingRequest(current, png_bytes, True, True))
        assert len(calls) == 1
        assert calls[0].headers["content-type"].startswith("multipart/form-data;")
