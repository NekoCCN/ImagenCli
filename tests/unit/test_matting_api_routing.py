"""CLI wiring: three mutually exclusive matting paths and their schema."""

from typing import Any, cast

import imagen_cli.application.matting_api as matting_api
from imagen_cli.application.container import Container
from imagen_cli.cli.commands import postprocess
from imagen_cli.cli.registry import discover

ARTIFACT = "art_" + "a" * 32


def _schema() -> dict[str, Any]:
    contract = discover("matting run")
    properties = cast(dict[str, Any], contract["input_schema"]["properties"])
    return properties


def test_model_accepts_local_names_and_vendor_paths():
    model = _schema()["model"]
    assert "enum" not in model
    assert model["type"] == "string"
    assert model["default"] == "birefnet-lite"


def test_api_mating_options_are_advertised():
    schema = _schema()
    assert schema["accept_api"]["pattern"] == "^[0-9a-f]{64}$"
    assert schema["resolution"]["minimum"] == 512
    assert schema["mask_blur"]["default"] == 0
    assert schema["mask_offset"]["default"] == 0
    assert schema["refine_fg"]["default"] is False
    assert schema["replicate_key_ref"]["default"] == "env:REPLICATE_API_TOKEN"


def test_api_model_with_remote_is_rejected(tmp_path):
    with Container(workspace=str(tmp_path / "ws")) as container:
        status, data = container.execute("matting run", {
            "artifact": ARTIFACT, "model": "sprited/birefnet-toonout", "remote": "salad",
        })
    assert status == "validation_error"
    assert data["error"]["type"] == "validation_error"


def test_api_model_with_explicit_local_backend_is_rejected(tmp_path):
    with Container(workspace=str(tmp_path / "ws")) as container:
        status, _ = container.execute("matting run", {
            "artifact": ARTIFACT, "model": "sprited/birefnet-toonout", "backend": "cuda",
        })
    assert status == "validation_error"


def test_api_model_routes_to_the_api_path(tmp_path, monkeypatch):
    captured: dict[str, Any] = {}

    def fake(repositories, store, artifact, **kwargs):
        captured.update(kwargs, artifact=artifact)
        return "succeeded", {}

    monkeypatch.setattr(matting_api, "run", fake)
    with Container(workspace=str(tmp_path / "ws")) as container:
        status, _ = container.execute("matting run", {
            "artifact": ARTIFACT, "model": "sprited/birefnet-toonout",
            "resolution": 1024, "accept_api": "b" * 64,
        })
    assert status == "succeeded"
    assert captured["model"] == "sprited/birefnet-toonout"
    assert captured["resolution"] == 1024
    assert captured["accept_api"] == "b" * 64
    assert captured["key_ref"] == "env:REPLICATE_API_TOKEN"
    # The schema default is 900s; a vendor queue must not be cut at 300.
    assert captured["timeout"] == 900.0


def test_local_model_still_uses_the_local_pipeline(tmp_path, monkeypatch):
    captured: dict[str, Any] = {}

    def fake_local(repositories, store, artifact, **kwargs):
        captured.update(kwargs, artifact=artifact)
        return "succeeded", {}

    def forbidden(*_args, **_kwargs):
        raise AssertionError("a local model must not reach the API path")

    monkeypatch.setattr(postprocess, "transform_", fake_local)
    monkeypatch.setattr(matting_api, "run", forbidden)
    with Container(workspace=str(tmp_path / "ws")) as container:
        status, _ = container.execute("matting run", {"artifact": ARTIFACT})
    assert status == "succeeded"
    assert captured["op_name"] == "matte"
    assert "model" not in captured["model_params"] or (
        captured["model_params"]["model"] == "birefnet-lite"
    )


def test_unknown_model_is_a_validation_error(tmp_path):
    with Container(workspace=str(tmp_path / "ws")) as container:
        status, data = container.execute("matting run", {
            "artifact": ARTIFACT, "model": "nobody/nothing",
        })
    assert status == "validation_error"
    assert "sprited/birefnet-toonout" in data["error"]["detail"]
