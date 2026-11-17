import io

import pytest
from PIL import Image

from imagen_cli.providers.base import ReferenceInput
from imagen_cli.providers.novelai_references import map_references, prepare_precise_reference


def picture(size):
    out = io.BytesIO()
    Image.new("RGB", size, "red").save(out, format="PNG")
    return out.getvalue()


@pytest.mark.parametrize(
    "size,canvas",
    [((100, 200), (1024, 1536)), ((200, 100), (1536, 1024)), ((100, 100), (1472, 1472))],
)
def test_precise_fit_pad_is_deterministic(size, canvas):
    result = prepare_precise_reference(picture(size))
    assert result == prepare_precise_reference(picture(size))
    image = Image.open(io.BytesIO(result))
    assert image.size == canvas
    assert image.getpixel((canvas[0] // 2, canvas[1] // 2)) == (255, 0, 0)
    if size[0] != size[1]:
        assert image.getpixel((0, 0)) == (0, 0, 0)


def test_raw_vibe_order_duplicates_and_settings():
    image = picture((10, 10))
    refs = (
        ReferenceInput(image, "vibe", {"strength": 0.3}),
        ReferenceInput(image, "vibe", {"strength": 0.7}),
    )
    result = map_references(refs, mode="raw", model="custom")
    assert len(result["reference_image_multiple"]) == 2
    assert result["reference_strength_multiple"] == [0.3, 0.7]
    with pytest.raises(TypeError):
        refs[0].settings["strength"] = 1


def test_encoded_requires_verified_preparation_and_mixed_roles_refused():
    image = picture((10, 10))
    with pytest.raises(ValueError):
        map_references((ReferenceInput(image, "vibe", {}),), mode="encoded", model="x")
    with pytest.raises(ValueError):
        map_references(
            (ReferenceInput(image, "vibe", {}), ReferenceInput(image, "precise", {})),
            mode="raw",
            model="nai-diffusion-4-5-full",
        )


@pytest.mark.parametrize(
    "settings",
    [{"strength": -1}, {"strength": float("nan")}, {"strength": True}, {"url": "file:/etc/passwd"}],
)
def test_invalid_settings_refused(settings):
    with pytest.raises(ValueError):
        map_references(
            (ReferenceInput(picture((10, 10)), "vibe", settings),), mode="raw", model="x"
        )


def test_precise_arrays_aligned_and_model_unverified_refused():
    refs = (
        ReferenceInput(picture((20, 10)), "precise", {"mode": "character", "strength": 0.2}),
        ReferenceInput(picture((10, 20)), "precise", {"mode": "character&style", "fidelity": 0.4}),
    )
    result = map_references(refs, mode="encoded", model="nai-diffusion-4-5-full")
    assert all(len(value) == 2 for value in result.values())
    assert [
        item["caption"]["base_caption"] for item in result["director_reference_descriptions"]
    ] == ["character", "character&style"]
    assert result["director_reference_strength_values"] == [0.2, 1]
    assert result["director_reference_secondary_strength_values"] == [1, 0.4]
    with pytest.raises(ValueError, match="unverified"):
        map_references(refs, mode="encoded", model="custom-model")


def test_reference_parameter_strings_never_read_files_or_network(monkeypatch):
    from pathlib import Path

    import httpx

    def forbidden(*args, **kwargs):
        pytest.fail("parameter strings must never cause a file or network read")

    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    with pytest.raises(ValueError):
        map_references(
            (ReferenceInput(b"x", "vibe", {"image": "file:/etc/passwd"}),), mode="raw", model="x"
        )


def test_adapter_maps_raw_vibe_separately_from_primary(png_bytes):
    import base64
    import json
    from dataclasses import replace

    import httpx

    from imagen_cli.domain.models import NovelAIOptions
    from imagen_cli.providers.novelai import NovelAIAdapter
    from tests.contract.test_provider_adapters import profile, request

    target = profile("novelai", "generate_image", "https://example.test", "custom")
    target = target.model_copy(
        update={"novelai_options": NovelAIOptions(reference_transport="raw")}
    )
    calls = []

    def handler(req):
        body = json.loads(req.content)
        params = body["parameters"]
        assert base64.b64decode(params["image"]) == png_bytes
        assert [base64.b64decode(item) for item in params["reference_image_multiple"]] == [
            png_bytes,
            png_bytes,
        ]
        assert params["reference_strength_multiple"] == [0.2, 0.8]
        calls.append(req)
        return httpx.Response(
            200, json={"images": [{"image": base64.b64encode(png_bytes).decode()}]}
        )

    approved = request(
        target,
        operation="edit",
        inputs=(png_bytes,),
        parameters={"size": "64x64", "count": 1, "strength": 0.5, "noise": 0.1},
    )
    approved = replace(
        approved,
        references=(
            ReferenceInput(png_bytes, "vibe", {"strength": 0.2}),
            ReferenceInput(png_bytes, "vibe", {"strength": 0.8}),
        ),
    )
    NovelAIAdapter(
        target, resolve_credential=lambda _: "test-only", transport=httpx.MockTransport(handler)
    ).create(approved)
    assert len(calls) == 1


def test_precise_dispatch_uses_frozen_verified_upload(monkeypatch, png_bytes):
    import base64

    from imagen_cli.providers import novelai_references

    bound = novelai_references.bind_precise_reference(ReferenceInput(png_bytes, "precise", {}))
    monkeypatch.setattr(
        novelai_references,
        "prepare_precise_reference",
        lambda _: pytest.fail("dispatch must use frozen verified bytes"),
    )
    result = map_references(
        (bound,), mode="encoded", model="nai-diffusion-4-5-full", require_approved=True
    )
    assert base64.b64decode(result["director_reference_images"][0]) == bound.prepared_content


@pytest.mark.parametrize("drift", ["unbound", "bytes", "version", "dimensions", "hash"])
def test_precise_dispatch_refuses_unapproved_transformation_before_credentials(png_bytes, drift):
    from dataclasses import replace

    from imagen_cli.providers.base import ProviderFailure
    from imagen_cli.providers.novelai import NovelAIAdapter
    from imagen_cli.providers.novelai_references import bind_precise_reference
    from tests.contract.test_provider_adapters import profile, request

    raw = ReferenceInput(png_bytes, "precise", {})
    bound = bind_precise_reference(raw)
    if drift == "unbound":
        bound = raw
    elif drift == "bytes":
        bound = replace(bound, prepared_content=png_bytes)
    else:
        evidence = dict(bound.transformation)
        evidence[{"version": "algorithm", "dimensions": "width", "hash": "sha256"}[drift]] = (
            "changed"
        )
        bound = replace(bound, transformation=evidence)
    target = profile("novelai", "generate_image", "https://example.test", "nai-diffusion-4-5-full")
    approved = replace(request(target), references=(bound,))
    with pytest.raises(ProviderFailure, match="validation_error"):
        NovelAIAdapter(
            target, resolve_credential=lambda _: pytest.fail("credential resolution")
        ).create(
            approved,
        )
