"""Offline NovelAI parameter boundary, independent of service-specific caps."""
import pytest

from imagen_cli.providers import contracts, vocabulary


def normalize(parameters, model="unverified-model", operation="generate"):
    from imagen_cli.providers.novelai_parameters import normalize_parameters
    return normalize_parameters(parameters, model=model, operation=operation)


def test_defaults_are_minimal_and_model_independent():
    assert normalize({}) == {"n_samples": 1}
    assert normalize({"steps": 1000}, model="nai-diffusion-4-full") == {
        "steps": 1000, "n_samples": 1,
    }


def test_scalar_mapping_does_not_rewrite_prompts():
    assert normalize({"size": "832x1216", "count": 1, "scale": 5.5,
                      "negative_prompt": "", "seed": 0}) == {
        "width": 832, "height": 1216, "n_samples": 1, "scale": 5.5,
        "negative_prompt": "", "seed": 0,
    }


@pytest.mark.parametrize("parameters", [
    {"scale": float("nan")}, {"scale": float("inf")}, {"scale": True},
    {"count": True}, {"seed": False}, {"steps": 1.5}, {"width": 0},
    {"size": "0x1024"}, {"size": "auto"}, {"width": 512},
    {"size": "512x512", "width": 512, "height": 512},
    {"count": 2}, {"n_samples": 2}, {"seed": -1}, {"strength": 1.1},
    {"image": "blob"}, {"mask": "blob"}, {"headers": {}},
    {"base_url": "https://elsewhere.test"}, {"Authorization": "secret"},
    {"reference_image_multiple": ["blob"]}, {"sm": 1},
])
def test_invalid_or_upload_security_overrides_are_refused(parameters):
    with pytest.raises(ValueError):
        normalize(parameters)


def test_explicit_dimensions_and_edit_controls():
    assert normalize({"width": 513, "height": 769, "strength": 0.5, "noise": 0},
                     operation="edit") == {
        "width": 513, "height": 769, "strength": 0.5, "noise": 0, "n_samples": 1,
    }


def test_structured_prompt_is_preserved_and_copied():
    prompt = {"caption": {"base_caption": "a cat", "char_captions": [
        {"char_caption": "red coat", "centers": [{"x": 0.5, "y": 1.0}]}]},
        "use_coords": True, "use_order": False}
    result = normalize({"v4_prompt": prompt, "v4_negative_prompt": prompt})
    assert result["v4_prompt"] == prompt
    assert result["v4_prompt"] is not prompt


@pytest.mark.parametrize("prompt", [
    {"caption": {"base_caption": "", "char_captions": [], "image": "blob"}},
    {"caption": {"base_caption": "", "char_captions": [
        {"char_caption": "a", "centers": [{"x": True, "y": 0}]}]}},
    {"caption": {"base_caption": "", "char_captions": [
        {"char_caption": "a", "centers": [{"x": 1.1, "y": 0}]}]}},
    {"caption": {"base_caption": "a" * 65537, "char_captions": []}},
    {"caption": {"base_caption": "", "char_captions": [
        {"char_caption": "a", "centers": []}] * 65}},
])
def test_structured_prompt_is_bounded(prompt):
    with pytest.raises(ValueError):
        normalize({"v4_prompt": prompt})


def test_vocabulary_delegates_but_does_not_claim_model_support():
    table = vocabulary.specs("novelai", "generate_image", "custom")
    assert table and {"scale", "v4_prompt", "size", "count"} <= {s.name for s in table}
    assert vocabulary.check("novelai", "generate_image", "custom", "generate",
                            {"negative_prompt": "", "scale": 5.5}) is None
    assert vocabulary.check("novelai", "generate_image", "custom", "generate",
                            {"scale": True}) is not None
    assert not vocabulary.serves("novelai", "generate_image", "custom")
    contract = contracts.contract("novelai", "generate_image")
    assert contract.transport == "sync" and contract.idempotency_scope == "none"
    assert contract.cancellation == "unsupported"
