"""Independent model-processing command shares transform's safety and storage path."""

import io
import json

from imagen_cli.cli.entrypoint import main
from imagen_cli.cli.registry import discover


def test_superres_run_is_a_separate_discoverable_entry(tmp_path):
    schema = discover("superres run")["input_schema"]
    assert {"artifact", "model", "budget_approval"} <= schema["properties"].keys()
    assert "model" in schema["required"]
    output = io.StringIO()
    code = main(["--workspace", str(tmp_path), "superres", "run",
                 "art_" + "a" * 32, "--model", "film-degrainer",
                 "--agent", "--no-interactive"], stdout=output, stderr=io.StringIO())
    assert code == 2 or code == 4
    value = json.loads(output.getvalue())
    assert "Unknown command" not in value["error"]["message"]


def test_transform_page_does_not_offer_model_inference(tmp_path):
    import asyncio

    from PIL import Image
    from textual.app import App

    from imagen_cli.cli.tui.app import Deps, TransformScreen, WorkbenchApp

    path = tmp_path / "source.png"
    Image.new("RGB", (8, 8)).save(path)

    class Harness(App):
        CSS = WorkbenchApp.CSS

    async def exercise():
        app = Harness()
        async with app.run_test(size=(80, 24)):
            screen = TransformScreen(Deps(None, None), "art", "ses", path)
            await app.push_screen(screen)
            assert all(not any(s["op"] == "superres" for s in screen._pipeline(name))
                       for name in screen.names)

    asyncio.run(exercise())
