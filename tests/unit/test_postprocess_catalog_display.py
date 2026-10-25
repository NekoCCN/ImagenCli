"""Human catalog presentation must not expose compatibility aliases as recipes."""

from imagen_cli.application import operator_catalog as catalog
from imagen_cli.cli.human import render


def test_browser_hides_duplicate_recipes_and_uses_chinese_labels():
    entries = catalog.browse_entries()
    keys = [entry["key"] for entry in entries]
    assert "像素化" in keys
    assert "像素复古风 (Pixel Art)" not in keys
    assert "赛璐璐手绘 (Cel Shade)" not in keys
    assert "guided_smooth" in keys
    for entry in entries:
        assert not any(char.isascii() and char.isalpha() for char in entry["name"])
    by_key = {entry["key"]: entry for entry in entries}
    assert by_key["guided_smooth"]["group"] == "平滑与缺陷"
    assert by_key["二次元平涂去杂 (Anime Cel Surface Smoothing)"]["group"] == "综合配方"
    assert "像素复古风 (Pixel Art)" in catalog.PRESET_PIPELINES


def test_cli_human_catalog_lists_all_entries_without_ellipsis():
    data = {"operations": ["guided_smooth"], "presets": list(catalog.PRESET_PIPELINES)}
    for color in (False, True):
        text = render({"ok": True, "status": "succeeded", "data": data}, columns=80, color=color)
        assert "综合配方" in text
        assert "引导保边平滑" in text
        assert "local_laplacian_sampled" in text
        assert "Anime Cel Master" not in text
        assert "…" not in text
