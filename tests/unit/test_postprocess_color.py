"""ICC conversion is explicit and malformed profiles are never silently discarded."""

import pytest
from PIL import Image, ImageCms

from imagen_cli.application import postprocess_service


def test_untagged_image_records_srgb_assumption():
    image = Image.new("RGBA", (3, 2), (90, 120, 150, 120))
    result, record = postprocess_service.normalize_color(image)
    assert result.tobytes() == image.tobytes()
    assert record["input_profile"] == "assumed_srgb"
    assert record["working_space"] == "srgb"


def test_srgb_icc_preserves_alpha_and_records_profile_hash():
    image = Image.new("RGBA", (3, 2), (90, 120, 150, 120))
    image.info["icc_profile"] = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    result, record = postprocess_service.normalize_color(image)
    assert result.getchannel("A").tobytes() == image.getchannel("A").tobytes()
    assert len(record["input_profile_sha256"]) == 64
    assert record["backend"] == "pillow_imagecms"


def test_malformed_icc_is_rejected():
    image = Image.new("RGB", (2, 2))
    image.info["icc_profile"] = b"broken profile"
    with pytest.raises(ValueError, match="ICC"):
        postprocess_service.normalize_color(image)


def test_chroma_transfer_rejects_reference_with_broken_icc(tmp_path):
    from imagen_cli.application.filters_restoration import chroma_transfer

    reference = tmp_path / "reference.png"
    Image.new("RGB", (3, 3), "red").save(reference, icc_profile=b"broken profile")
    with pytest.raises(ValueError, match="ICC"):
        chroma_transfer(Image.new("RGB", (3, 3), "blue"), reference=reference)


@pytest.mark.parametrize("mode", ["CMYK", "I", "F", "I;16", "LAB"])
def test_unsupported_untagged_color_modes_are_not_silently_downconverted(mode):
    with pytest.raises(ValueError, match="color mode|ICC"):
        postprocess_service.normalize_color(Image.new(mode, (2, 2)))


def test_output_color_conversion_declares_srgb_profile():
    result, _ = postprocess_service.normalize_color(Image.new("RGB", (2, 2)))
    profile = ImageCms.ImageCmsProfile(__import__("io").BytesIO(result.info["icc_profile"]))
    assert "sRGB" in ImageCms.getProfileDescription(profile)
