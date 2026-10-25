"""Plain, evidence-oriented wording for the postprocess catalog."""

from imagen_cli.application.operator_catalog import INTROS, browse_entries, get_intro_text


def test_postprocess_surfaces_are_marked_experimental(tmp_path):
    from imagen_cli.cli.human import render
    from imagen_cli.cli.tui.app import TransformScreen

    screen = TransformScreen(None, "art_test", "ses_test", tmp_path / "missing.png")
    assert "experimental" in str(screen.header_title.render())
    text = render({"ok": True, "data": {"presets": ["像素化"]}})
    assert "experimental" in text


def test_all_legacy_browser_entries_have_specific_tradeoffs():
    for entry in browse_entries():
        if not entry["key"].isascii():
            intro = INTROS[entry["key"]]
            assert intro["benefit"], entry["key"]
            assert intro["loss"], entry["key"]
            assert intro["title"] == entry["name"]


def test_intro_uses_plain_effect_and_tradeoff_labels():
    text = get_intro_text("去AI塑料感")
    assert "操作：" in text
    assert "可能的优势：" in text
    assert "可能的缺点：" in text
    assert "可能用来：" in text
    assert "Anti-AI" not in text
    assert "适用于" not in text
    assert "推荐" not in text


def test_every_intro_has_a_use_case_and_tradeoffs():
    from imagen_cli.application import operator_catalog_en

    for source in (INTROS, operator_catalog_en.INTROS):
        for key, intro in source.items():
            assert intro.get("use_case"), key
            assert intro.get("benefit"), key
            assert intro.get("loss"), key


def test_style_names_describe_operations_not_aesthetic_claims():
    text = get_intro_text("日系通透空气感")
    assert "操作：" in text
    assert "可能的优势：" in text
    assert "可能的缺点：" in text
    assert "可能用来：" in text
    assert "风格" not in text
