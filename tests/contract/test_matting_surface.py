"""SKILLS, TUI and CLI must describe the same matting surface.

The regression this locks: a new local model family that reaches the CLI but not
the installed skills, or one whose TUI panel lists a different set of models than
the CLI catalog, leaves an agent and a human looking at two different products.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from imagen_cli.application import matting, operator_catalog, superres
from imagen_cli.cli.registry import COMMANDS, discover

BUNDLED = Path(__file__).resolve().parents[2] / "src" / "imagen_cli" / "bundled_skills"
SKILL = BUNDLED / "imagen-assets" / "SKILL.md"

MATTING_COMMANDS = (
    "matting run",
    "matting models",
    "matting doctor",
    "matting install",
    "matting fetch",
)


def test_every_matting_command_reaches_the_skill():
    text = SKILL.read_text(encoding="utf-8")
    for name in MATTING_COMMANDS:
        assert name in text, f"{name} is missing from the bundled skill"


def test_matting_commands_are_available_everywhere():
    for name in MATTING_COMMANDS:
        assert COMMANDS[name].available, name


def test_skill_examples_run_against_the_parser():
    """Every matting example in the skill must parse; guides are English-only."""
    import shlex

    from imagen_cli.cli.entrypoint import _parse

    text = SKILL.read_text(encoding="utf-8")
    placeholders = {
        "ARTIFACT_ID": "art_" + "a" * 32,
        "NAME": "birefnet-lite",
        "DIGEST": "b" * 64,
    }
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("imagen matting"):
            continue
        argv = shlex.split(stripped)[1:]
        argv = [placeholders.get(token, token) for token in argv]
        argv = [token for token in argv if token != "--agent"]
        command, arguments, _options = _parse(argv)
        assert command.startswith("matting "), stripped


def test_skill_guides_stay_english():
    for path in BUNDLED.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        assert not any("\u4e00" <= char <= "\u9fff" for char in text), str(path)


def test_skill_names_only_shipped_models():
    """A skill that names a model the catalog does not ship sends agents to a 404."""
    text = SKILL.read_text(encoding="utf-8")
    for name in matting.MANIFEST:
        assert name in text, name
    # The non-commercial export must stay out of the advertised surface.
    assert "rmbg" not in text.lower()


def test_skill_states_the_licence_boundary():
    text = SKILL.read_text(encoding="utf-8").lower()
    assert "mit" in text
    assert "non-commercial" in text or "by-nc" in text


def test_tui_panel_reads_the_same_two_catalogs():
    """The panel is the CLI catalog plus the matting catalog, not a third list."""
    from imagen_cli.cli.tui.app import _family_cell, _weights_cells

    expected = [
        *((row, "superres") for row in superres.catalog()["models"]),
        *((row, "matting") for row in matting.catalog()["models"]),
    ]
    rendered = []
    for row, family in expected:
        cells = _weights_cells(row)
        rendered.append((cells[0], _family_cell(family), cells[3]))
    assert [name for name, _family, _state in rendered] == [
        row["model"] for row, _family in expected
    ]
    assert {family for _name, family, _state in rendered} == {"超分", "抠图"}


def test_matting_presets_are_discoverable_in_the_catalog():
    presets = operator_catalog.PRESET_PIPELINES
    for key in ("抠图贴纸 (Cutout Sticker)", "抠图虚化 (Cutout Bokeh)"):
        assert key in presets
        assert presets[key][0]["op"] == "matte"
    entries = {entry["key"] for entry in operator_catalog.browse_entries()}
    assert {"抠图贴纸 (Cutout Sticker)", "抠图虚化 (Cutout Bokeh)"} <= entries


def test_matte_operator_page_matches_the_schema():
    """The catalog's parameter names must be the ones the CLI accepts."""
    item = operator_catalog.get_operator_item("matte")
    assert item is not None
    schema = discover("matting run")["input_schema"]["properties"]
    for parameter in item.parameters:
        assert parameter.name in schema, parameter.name
    for name in ("model", "tile", "overlap", "feather"):
        assert name in schema


@pytest.mark.parametrize("preset", ["抠图贴纸 (Cutout Sticker)", "抠图虚化 (Cutout Bokeh)"])
def test_preset_pipeline_validates(preset):
    from imagen_cli.application import postprocess_service

    steps = postprocess_service.validate(operator_catalog.PRESET_PIPELINES[preset])
    assert [step["op"] for step in steps][0] == "matte"


def test_readme_documents_matting_in_both_languages():
    root = Path(__file__).resolve().parents[2]
    english = (root / "README.md").read_text(encoding="utf-8")
    chinese = (root / "README.zh-CN.md").read_text(encoding="utf-8")
    for text in (english, chinese):
        assert "imagen matting run" in text
        assert "birefnet-lite" in text
    assert "Local matting" in english
    assert "本地抠图" in chinese
