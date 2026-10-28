"""One parameter vocabulary shared by planning, the CLI surface and the TUI.

Every value here is copied from a vendor reference checked 2026-10-28; the
module docstring of ``imagen_cli.providers.vocabulary`` lists the sources.
These tests pin the vendor shape, so a provider change must fail here first.
"""

import pytest

from imagen_cli.providers.vocabulary import check, specs


def openai(model: str, **parameters: object) -> str | None:
    return check("openai", "images", model, "generate", {"count": 1, **parameters})


def gemini(model: str, **parameters: object) -> str | None:
    return check("gemini", "generate_content", model, "generate", {"count": 1, **parameters})


@pytest.mark.parametrize(
    "size", ["1024x1024", "1536x1024", "1024x1536", "auto", "1536x864", "3840x2160"]
)
def test_gpt_image_2_accepts_standard_and_arbitrary_resolutions(size: str) -> None:
    assert openai("gpt-image-2.5-sunburst", size=size) is None


@pytest.mark.parametrize("size", ["1537x864", "1536x865", "1023x1023", "1920x1081"])
def test_arbitrary_resolution_must_sit_on_the_sixteen_grid(size: str) -> None:
    assert openai("gpt-image-2.5-sunburst", size=size) is not None


@pytest.mark.parametrize("size", ["512x2048", "2048x512", "4096x1024", "1024x4096"])
def test_arbitrary_resolution_must_stay_between_one_to_three_and_three_to_one(size: str) -> None:
    assert openai("gpt-image-2.5-sunburst", size=size) is not None


@pytest.mark.parametrize("size", ["256x256", "1280x256", "5120x2880"])
def test_arbitrary_resolution_must_fit_the_documented_pixel_and_edge_limits(size: str) -> None:
    assert openai("gpt-image-2.5-sunburst", size=size) is not None


def test_older_image_models_keep_the_four_documented_values_only() -> None:
    assert openai("gpt-image-1.5", size="1024x1536") is None
    assert openai("gpt-image-1.5", size="1536x864") is not None


def test_retired_image_model_ids_are_refused() -> None:
    for model in ("gpt-image-1", "dall-e-2", "dall-e-3"):
        assert openai(model) is not None


def test_current_image_model_ids_are_accepted() -> None:
    for model in ("gpt-image-2", "gpt-image-2.5-sunburst", "gpt-image-2.5-flare", "gpt-image-1.5"):
        assert openai(model) is None


def test_quality_tiers_follow_the_model_generation() -> None:
    assert openai("gpt-image-2.5-sunburst", quality="xhigh") is None
    assert openai("gpt-image-2.5-sunburst", quality="max") is None
    assert openai("gpt-image-1.5", quality="high") is None
    assert openai("gpt-image-1.5", quality="xhigh") is not None


def test_pixel_size_is_named_size_and_carries_no_aspect_ratio_field() -> None:
    assert openai("gpt-image-2.5-sunburst", aspect_ratio="16:9") is not None


def test_count_bound_is_the_documented_ten() -> None:
    assert check("openai", "images", "gpt-image-2.5-sunburst", "generate", {"count": 10}) is None
    assert (
        check("openai", "images", "gpt-image-2.5-sunburst", "generate", {"count": 11})
        is not None
    )


def test_input_fidelity_belongs_to_edit_only() -> None:
    assert check(
        "openai", "images", "gpt-image-2.5-sunburst", "edit",
        {"count": 1, "input_fidelity": "high"},
    ) is None
    assert openai("gpt-image-2.5-sunburst", input_fidelity="high") is not None


@pytest.mark.parametrize("size", ["512", "1K", "2K", "4K"])
def test_flash_image_model_accepts_every_documented_image_size(size: str) -> None:
    assert gemini("gemini-3.1-flash-image", image_size=size) is None


@pytest.mark.parametrize("size", ["1k", "2k", "2048", "1024x1024", "8K"])
def test_image_size_rejects_anything_outside_the_documented_tiers(size: str) -> None:
    assert gemini("gemini-3.1-flash-image", image_size=size) is not None


def test_lite_model_only_offers_one_kilopixel() -> None:
    assert gemini("gemini-3.1-flash-lite-image", image_size="1K") is None
    assert gemini("gemini-3.1-flash-lite-image", image_size="2K") is not None


def test_nano_banana_two_point_one_has_no_half_kilopixel_tier() -> None:
    assert gemini("gemini-nano-banana-2.1", image_size="4K") is None
    assert gemini("gemini-nano-banana-2.1", image_size="512") is not None


def test_gemini_2_5_flash_image_has_no_size_control() -> None:
    assert gemini("gemini-2.5-flash-image") is None
    assert gemini("gemini-2.5-flash-image", image_size="2K") is not None


@pytest.mark.parametrize(
    "ratio", ["1:1", "1:4", "4:1", "1:8", "8:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4",
              "9:16", "16:9", "21:9"]
)
def test_gemini_accepts_the_fourteen_documented_aspect_ratios(ratio: str) -> None:
    assert gemini("gemini-3.1-flash-image", aspect_ratio=ratio) is None


def test_gemini_refuses_an_undocumented_aspect_ratio() -> None:
    assert gemini("gemini-3.1-flash-image", aspect_ratio="5:3") is not None


def test_gemini_uses_ratio_and_tier_names_not_pixels() -> None:
    assert gemini("gemini-3.1-flash-image", size="1024x1024") is not None


def test_gemini_count_stays_at_one() -> None:
    assert check("gemini", "generate_content", "gemini-3.1-flash-image", "generate",
                 {"count": 1}) is None
    assert check("gemini", "generate_content", "gemini-3.1-flash-image", "generate",
                 {"count": 2}) is not None


def test_vocabulary_lists_what_a_route_supports_for_the_surface() -> None:
    openai_specs = specs("openai", "images", "gpt-image-2.5-sunburst", "generate") or ()
    gemini_specs = specs("gemini", "generate_content", "gemini-3.1-flash-image", "generate") or ()
    openai_names = {spec.name for spec in openai_specs}
    gemini_names = {spec.name for spec in gemini_specs}
    assert {"count", "size", "quality", "background"} <= openai_names
    assert {"count", "aspect_ratio", "image_size"} == gemini_names


def test_routes_without_a_reviewed_vocabulary_are_left_alone() -> None:
    assert specs("acme", "images", "mystery", "generate") is None
    assert check("acme", "images", "mystery", "generate", {"anything": "goes"}) is None


def test_the_refusal_says_what_the_route_accepts() -> None:
    reason = openai("gpt-image-2.5-sunburst", size="1920x1081")
    assert reason is not None and "16" in reason
    ratio_reason = gemini("gemini-3.1-flash-image", aspect_ratio="5:3")
    assert ratio_reason is not None and "16:9" in ratio_reason
