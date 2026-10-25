"""Human catalog presentation must not expose compatibility aliases as recipes."""

import pytest

from imagen_cli.application import operator_catalog as catalog
from imagen_cli.application.container import Container
from imagen_cli.cli.human import render


def test_browser_lists_recipes_and_hides_default_parameter_aliases():
    entries = catalog.browse_entries()
    keys = [entry["key"] for entry in entries]
    assert "像素化" not in keys  # identical to `--op pixelate`
    assert "guided_smooth" not in keys  # identical to `--op guided_smooth`
    assert "像素复古风 (Pixel Art)" not in keys  # duplicate pipeline
    assert "赛璐璐手绘 (Cel Shade)" not in keys
    assert "去AI塑料感 (Anti-AI Organic)" in keys
    for entry in entries:
        assert not any(char.isascii() and char.isalpha() for char in entry["name"])
    by_key = {entry["key"]: entry for entry in entries}
    assert by_key["二次元平涂去杂 (Anime Cel Surface Smoothing)"]["group"] == "综合配方"


def test_interactive_browser_can_still_reach_every_alias():
    entries = catalog.browse_entries(include_aliases=True)
    keys = [entry["key"] for entry in entries]
    assert "guided_smooth" in keys
    by_key = {entry["key"]: entry for entry in entries}
    assert by_key["guided_smooth"]["group"] == "平滑与缺陷"
    assert "像素复古风 (Pixel Art)" in catalog.PRESET_PIPELINES
    assert "像素化" in catalog.PRESET_PIPELINES


def test_listed_presets_are_compositions_and_the_rest_are_single_operators():
    listed = {entry["key"] for entry in catalog.browse_entries()}
    for key, pipeline in catalog.PRESET_PIPELINES.items():
        if key in listed:
            assert len(pipeline) > 1, key
        else:
            assert len(pipeline) == 1, key


def test_tuned_single_operator_preset_surfaces_on_the_operator_page(tmp_path):
    with Container(workspace=str(tmp_path / "ws")) as container:
        status, data = container.execute("ops", {"op": "quantize"})
    assert status == "succeeded"
    variants = data["details"]["quantize"]["variants"]
    assert {"preset": "调色板", "params": {"colors": 16, "dither": True}} in variants


def test_cli_human_catalog_lists_recipes_and_operators_separately():
    data = {"operations": ["guided_smooth", "blur"], "presets": list(catalog.PRESET_PIPELINES)}
    for color in (False, True):
        text = render({"ok": True, "status": "succeeded", "data": data}, columns=80, color=color)
        assert "综合配方" in text
        assert "去AI塑料感" in text
        assert "guided_smooth" in text  # listed once, as an operator
        assert "引导保边平滑" not in text  # the alias is not a second entry
        assert "Anime Cel Master" not in text
        assert "…" not in text


def test_operator_detail_explains_each_simple_parameter(tmp_path):
    with Container(workspace=str(tmp_path / "ws")) as container:
        status, data = container.execute("ops", {"op": "de_ai_bloom"})
    assert status == "succeeded"
    detail = data["details"]["de_ai_bloom"]
    parameters = {row["name"]: row for row in detail["parameters"]}
    assert parameters["radius"]["type"] == "int"
    assert (parameters["radius"]["min"], parameters["radius"]["max"]) == (1, 40)
    assert parameters["radius"]["description"]
    assert detail["example"].startswith("imagen transform ARTIFACT --op 'de_ai_bloom:")


def test_uncatalogued_operator_still_lists_its_parameters(tmp_path):
    with Container(workspace=str(tmp_path / "ws")) as container:
        status, data = container.execute("ops", {"op": "blur"})
    assert status == "succeeded"
    assert [row["name"] for row in data["details"]["blur"]["parameters"]] == ["radius"]


def test_preset_detail_lists_every_step_with_its_parameters(tmp_path):
    with Container(workspace=str(tmp_path / "ws")) as container:
        status, data = container.execute("ops", {"preset": "去AI塑料感"})
    assert status == "succeeded"
    detail = data["details"]["去AI塑料感 (Anti-AI Organic)"]
    assert [step["op"] for step in detail["steps"]] == [
        "surface_blur",
        "filmic_tone",
        "de_ai_bloom",
    ]
    assert all(step["parameters"] for step in detail["steps"])


@pytest.mark.parametrize(
    ("arguments", "missing"), [({"op": "nosuch"}, "nosuch"), ({"preset": "nosuch"}, "nosuch")]
)
def test_unknown_identifier_is_refused_and_named(tmp_path, arguments, missing):
    with Container(workspace=str(tmp_path / "ws")) as container:
        with pytest.raises(ValueError, match=missing):
            container.execute("ops", arguments)


def test_human_detail_shows_parameter_rows_and_an_example():
    value = {
        "ok": True,
        "status": "succeeded",
        "data": {
            "details": {
                "de_ai_bloom": {
                    "title": "Bloom",
                    "op": "de_ai_bloom",
                    "parameters": [
                        {
                            "name": "radius",
                            "type": "int",
                            "default": 8,
                            "min": 1,
                            "max": 40,
                            "description": "Gaussian radius",
                        }
                    ],
                    "example": "imagen transform ARTIFACT --op 'de_ai_bloom:radius=8'",
                }
            }
        },
    }
    for color in (False, True):
        text = render(value, columns=100, color=color)
        assert "radius" in text
        assert "1–40" in text
        assert "8" in text
        assert "Gaussian radius" in text
        assert "de_ai_bloom:radius=8" in text
