"""Public operator metadata and historical recipe compatibility."""

import pytest

from imagen_cli.application.operator_catalog import (
    CATEGORIES,
    INTROS,
    PRESET_CATEGORIES,
    PRESET_PIPELINES,
    get_intro_text,
    get_operator_item,
)


@pytest.mark.parametrize(
    ("op", "expected", "unsupported"),
    [
        ("anime_line_enhance", "各向同性", ("流向", "定向", "FDoG")),
        ("subsurface_scattering", "RGB", ("Jensen", "光子", "物理模型")),
        ("color_temperature", "RGB", ("普朗克", "黑体", "物理准确")),
        ("filmic_tone", "幂", ("Hermite", "多项式拟合")),
        ("vignette", "径向", ("四次方", "Cosine-Fourth")),
    ],
)
def test_legacy_metadata_describes_actual_processing(op, expected, unsupported):
    item = get_operator_item(op)
    assert item is not None
    text = item.description + get_intro_text(op)
    assert expected in text
    assert not any(claim in text for claim in unsupported)


def test_category_labels_describe_tasks_without_universal_ai_claims():
    assert CATEGORIES["anti_ai"] == "平滑与缺陷"
    assert CATEGORIES["acg"] == "线条与色块"
    assert CATEGORIES["photo"] == "颜色明暗"


@pytest.mark.parametrize(
    "op",
    [
        "guided_smooth",
        "rolling_guidance",
        "flow_line",
        "kuwahara_anisotropic",
        "bounded_sharpen",
        "multiscale_detail",
        "linear_resize",
        "l0_smooth",
        "domain_transform",
        "wls_smooth",
        "rtv_smooth",
        "periodic_notch",
        "illumination_correct",
    ],
)
def test_new_operator_can_build_pipeline_from_its_displayed_defaults(op):
    item = get_operator_item(op)
    assert item is not None
    defaults = {parameter.name: parameter.default for parameter in item.parameters}
    assert defaults == item.default_params
    assert item.build_pipeline() == [{"op": op, "params": defaults}]
    assert item.description in get_intro_text(op)


def test_every_recipe_has_a_tui_category_for_filtering():
    missing = sorted(set(PRESET_PIPELINES) - set(PRESET_CATEGORIES))
    assert missing == []


def test_historical_recipe_and_line_controls_remain_available():
    steps = PRESET_PIPELINES["二次元线条强化与描墨 (Anime Line Inking)"]
    item = get_operator_item("anime_line_enhance")
    assert item is not None
    assert steps == item.build_pipeline()
    assert [parameter.name for parameter in item.basic_parameters] == ["strength", "thickness"]
    assert len(item.advanced_parameters) == 7


@pytest.mark.parametrize(
    "op",
    [
        "guided_smooth",
        "rolling_guidance",
        "flow_line",
        "kuwahara_anisotropic",
    ],
)
def test_new_algorithm_presets_have_stable_ascii_keys(op):
    item = get_operator_item(op)
    assert item is not None
    assert op.isascii()
    assert PRESET_PIPELINES[op] == item.build_pipeline()


@pytest.mark.parametrize(
    ("op", "overrides"),
    [
        ("guided_smooth", {"radius": 7, "epsilon": 0.002, "strength": 0.25}),
        ("rolling_guidance", {"iterations": 2, "sigma_range": 0.05}),
        ("flow_line", {"output": "lines", "strength": 0.8}),
        ("bounded_sharpen", {"noise_gate": 0.02, "overshoot": 0.1}),
        ("linear_resize", {"scale": 2.0, "filter": "box"}),
    ],
)
def test_pipeline_overrides_do_not_mutate_catalog_or_recipe(op, overrides):
    item = get_operator_item(op)
    assert item is not None
    before = item.build_pipeline()
    assert item.build_pipeline(overrides) == [
        {"op": op, "params": {**before[0]["params"], **overrides}}
    ]
    assert item.build_pipeline() == before
    assert PRESET_PIPELINES[op] == before


def test_new_filter_controls_publish_expected_parameter_units_and_choices():
    guided = get_operator_item("guided_smooth")
    flow = get_operator_item("flow_line")
    assert guided is not None and flow is not None
    assert guided.default_params == {
        "radius": 4,
        "epsilon": 0.001,
        "strength": 0.5,
        "guidance": "luma",
        "border": "reflect",
    }
    params = {parameter.name: parameter for parameter in guided.parameters}
    assert (params["radius"].min_value, params["radius"].max_value) == (1, 128)
    assert (params["epsilon"].min_value, params["epsilon"].max_value) == (1e-6, 1)
    assert {parameter.name: parameter.choices for parameter in flow.parameters}["output"] == [
        "overlay",
        "lines",
    ]


@pytest.mark.parametrize(
    "op",
    [
        "guided_smooth",
        "rolling_guidance",
        "flow_line",
        "kuwahara_anisotropic",
        "bounded_sharpen",
        "multiscale_detail",
        "linear_resize",
        "l0_smooth",
        "domain_transform",
        "wls_smooth",
        "rtv_smooth",
        "periodic_notch",
        "chroma_transfer",
        "illumination_correct",
    ],
)
def test_new_operators_have_explicit_introductions_and_task_categories(op):
    item = get_operator_item(op)
    assert item is not None
    assert INTROS[op]["summary"] == item.description
    assert INTROS[op]["principle"]
    assert INTROS[op]["scenarios"]
    assert PRESET_CATEGORIES[op] == item.category_label


def test_reference_transfer_requires_real_reference_and_has_no_default_recipe():
    item = get_operator_item("chroma_transfer")
    assert item is not None
    reference = next(p for p in item.parameters if p.name == "reference")
    assert reference.required is True
    assert reference.default is None
    assert "reference" not in item.default_params
    assert "chroma_transfer" not in PRESET_PIPELINES
    with pytest.raises(ValueError, match="reference"):
        item.build_pipeline()
    with pytest.raises(ValueError, match="reference"):
        item.build_pipeline({"reference": " "})
    assert item.build_pipeline({"reference": "reference.png"}) == [
        {
            "op": "chroma_transfer",
            "params": {"reference": "reference.png", "strength": 1.0, "luminance": "preserve"},
        }
    ]


@pytest.mark.parametrize(
    ("op", "terms"),
    [
        ("rtv_smooth", ("box", "IRLS", "近似")),
        ("periodic_notch", ("手动", "周期")),
        ("illumination_correct", ("Gaussian", "非 LIME")),
        ("l0_smooth", ("周期边界",)),
    ],
)
def test_algorithm_caveats_are_visible_in_introductions(op, terms):
    text = get_intro_text(op)
    assert all(term in text for term in terms)
