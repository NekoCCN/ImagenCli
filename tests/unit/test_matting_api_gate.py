"""The Replicate matting catalog and the approval digest that binds a request."""

import pytest

from imagen_cli.application import matting_api


def test_catalog_has_four_image_models_and_one_video_stub():
    assert set(matting_api.MODELS) == {
        "sprited/birefnet-toonout",
        "sprited/birefnet",
        "men1scus/birefnet",
        "m-kunz/birefnet_portrait",
        "sprited/birefnet-video",
    }
    assert matting_api.DEFAULT_MODEL == "sprited/birefnet-toonout"
    assert matting_api.MODELS["sprited/birefnet-toonout"]["implemented"] is True
    assert matting_api.MODELS["sprited/birefnet-video"]["implemented"] is False


def test_every_model_declares_the_fields_the_gate_needs():
    for name, entry in matting_api.MODELS.items():
        assert entry["license"], name
        assert entry["source"].startswith("https://replicate.com/"), name
        assert isinstance(entry["cost_note"], str) and entry["cost_note"], name
        assert entry["parameter_style"] in {"mask", "resolution_label", "none"}, name


def test_toonout_pins_the_reviewed_version():
    assert matting_api.MODELS["sprited/birefnet-toonout"]["version"] == (
        "35f7c78ef6338bf4cb94b41a83407ab44e2de7b436b8feafba547235d366a2bb"
    )


def test_digest_is_stable_and_binds_every_input():
    digest = matting_api.digest
    base = digest("a" * 64, "sprited/birefnet-toonout", {"resolution": 1024})
    assert base == digest("a" * 64, "sprited/birefnet-toonout", {"resolution": 1024})
    assert base != digest("b" * 64, "sprited/birefnet-toonout", {"resolution": 1024})
    assert base != digest("a" * 64, "sprited/birefnet", {"resolution": 1024})
    assert base != digest("a" * 64, "sprited/birefnet-toonout", {"resolution": 512})
    assert len(base) == 64


def test_digest_ignores_unset_parameters():
    digest = matting_api.digest
    assert digest("a" * 64, "sprited/birefnet", {"resolution": None}) == digest(
        "a" * 64, "sprited/birefnet", {}
    )


def test_describe_rejects_unknown_model():
    with pytest.raises(KeyError):
        matting_api.describe("nobody/nothing")


def test_translate_parameters_per_model_style():
    translate = matting_api.translate_parameters
    assert translate("sprited/birefnet-toonout",
                     {"resolution": 1024, "mask_blur": 2}) == {
        "resolution": 1024, "mask_blur": 2,
    }
    assert translate("men1scus/birefnet", {"resolution": 1024}) == {
        "resolution": "1024x1024",
    }
    assert translate("m-kunz/birefnet_portrait", {"resolution": 1024}) == {}
