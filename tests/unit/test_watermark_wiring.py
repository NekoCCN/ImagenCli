"""The watermark commands carry their choices into the worker and fail before publishing."""

import io

import pytest
from PIL import Image

from imagen_cli.application import remote_service, watermark_service
from imagen_cli.application.container import Container
from imagen_cli.application.session_service import SessionService
from imagen_cli.cli.entrypoint import _parse
from imagen_cli.cli.registry import discover

ARTIFACT = "art_" + "a" * 32
SESSION = "ses_" + "b" * 32


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (9, 8, 7)).save(buffer, format="PNG")
    return buffer.getvalue()


def _parsed(argv: list[str]) -> tuple[str, dict]:
    """`_parse` returns `list | dict` by contract; these commands always get a dict."""
    command, parameters, _ = _parse(argv)
    assert isinstance(parameters, dict)
    return command, parameters


def test_remote_watermark_forwards_pipeline_backend_and_session(tmp_path, monkeypatch):
    captured = {}

    def fake(repositories, store, artifact, name, operation, **kwargs):
        captured.update(kwargs, artifact=artifact, operation=operation)
        return "approval_required", {"job_digest": "a" * 64}

    monkeypatch.setattr(remote_service, "run_operation", fake)
    with Container(workspace=str(tmp_path / "ws")) as container:
        container.execute("watermark invisible", {
            "artifact": ARTIFACT, "remote": "modal", "pipeline": "sdxl-zimage",
            "session": SESSION, "max_seconds": 600, "cpu_offload": True,
        })
        assert captured["pipeline"] == "sdxl-zimage"
        assert captured["session"] == SESSION
        assert captured["max_seconds"] == 600
        assert captured["params"]["pipeline"] == "sdxl-zimage"
        container.execute("watermark visible", {
            "artifact": ARTIFACT, "remote": "modal", "backend": "lama",
        })
        assert captured["params"]["backend"] == "lama"


def test_local_watermark_uses_max_seconds_as_the_timeout(tmp_path, monkeypatch):
    captured = {}

    def fake(repositories, store, artifact, operation, **kwargs):
        captured.update(kwargs)
        return "succeeded", {}

    monkeypatch.setattr(watermark_service, "run_operation", fake)
    with Container(workspace=str(tmp_path / "ws")) as container:
        container.execute("watermark invisible", {
            "artifact": ARTIFACT, "max_seconds": 1500, "accept_license": True,
        })
        assert captured["timeout"] == 1500
        container.execute("watermark invisible", {
            "artifact": ARTIFACT, "accept_license": True,
        })
        assert captured["timeout"] == 900


def test_mark_takes_a_value():
    command, args = _parsed([
        "watermark", "visible", ARTIFACT, "--mark", "logo",
    ])
    assert command == "watermark visible"
    assert args["mark"] == "logo"


def test_an_ambiguous_session_publishes_nothing(tmp_path, monkeypatch):
    def fake_run(request, *, timeout):
        request["output"] and open(request["output"], "wb").write(_png())
        return {"status": "succeeded", "raiw_version": "0.53.0"}

    monkeypatch.setattr(watermark_service, "run", fake_run)
    with Container(workspace=str(tmp_path / "ws")) as container:
        service = SessionService(container.repositories, container.artifact_store)
        first = service.create_session("first")
        second = service.create_session("second")
        turn = service.create_turn(
            first.id, instruction="source", operation="generate",
            resolved_profile_snapshot={}, model="fixture", parameters={},
        )
        source = service.save_artifact(turn.id, _png(), origin_manifest={"kind": "fixture"})
        container.repositories.insert("artifact_links", {
            "session_id": second.id, "artifact_id": source.id,
            "role": "output", "created_at": source.created_at,
        })
        before = len(container.repositories.list("artifacts"))
        with pytest.raises(ValueError, match="--session"):
            watermark_service.run_operation(
                container.repositories, container.artifact_store,
                source.id, "watermark.visible",
            )
        assert len(container.repositories.list("artifacts")) == before


def test_new_commands_advertise_their_own_data_schema():
    for name in ("remote list", "remote job get", "watermark invisible",
                 "watermark status", "superres doctor"):
        schema = discover(name)["data_schema"]
        assert "commands" not in schema["properties"], name


def test_watermark_accepts_an_explicit_session():
    command, args = _parsed([
        "watermark", "visible", ARTIFACT, "--session", SESSION,
    ])
    assert command == "watermark visible"
    assert args["session"] == SESSION


def test_erase_parses_repeated_regions_and_fill_options():
    command, args = _parsed([
        "watermark", "erase", ARTIFACT,
        "--region", "4,8,16,12", "--region", "40,8,16,12",
        "--backend", "lama", "--inpaint-method", "ns", "--dilate", "5",
    ])
    assert command == "watermark erase"
    assert args["regions"] == ["4,8,16,12", "40,8,16,12"]
    assert args["backend"] == "lama"
    assert args["inpaint_method"] == "ns"
    assert args["dilate"] == 5


def test_all_and_classify_parse_their_options():
    command, args = _parsed([
        "watermark", "all", ARTIFACT, "--backend", "migan",
        "--sensitivity", "strict", "--pipeline", "sdxl-zimage",
        "--strength", "0.35", "--seed", "7", "--force",
    ])
    assert command == "watermark all"
    assert args["sensitivity"] == "strict"
    assert args["strength"] == 0.35
    assert args["seed"] == 7
    command, args = _parsed(["watermark", "classify", ARTIFACT, "--source"])
    assert command == "watermark classify"
    assert args["source"] is True


def test_setup_and_update_carry_the_requested_extras(monkeypatch, tmp_path):
    monkeypatch.setenv("IMAGEN_RAIW_ROOT", str(tmp_path))
    monkeypatch.setattr(watermark_service, "_imagen_version", lambda: "0.1.17")
    plan = watermark_service.setup(accept=False, extras="visible,qwen-zimage")
    assert "remove-ai-watermarks[visible,qwen-zimage]>=0.53,<0.54" in plan["packages"]
    with pytest.raises(ValueError, match="extra"):
        watermark_service.setup(accept=False, extras="visible,../../evil")


def test_resample_is_discoverable_with_its_own_schema():
    schema = discover("watermark resample")["input_schema"]
    assert schema["required"] == ["artifact"]
    assert schema["properties"]["model"]["default"] == "realesrgan-x2plus"
    assert "remote" not in schema["properties"]


def test_resample_rejects_remote(tmp_path):
    from jsonschema import ValidationError

    # The schema has no `remote`, so the request dies before any work starts.
    with Container(workspace=str(tmp_path / "ws")) as container:
        with pytest.raises(ValidationError):
            container.execute("watermark resample", {
                "artifact": ARTIFACT, "remote": "modal",
            })


def test_resample_carries_its_choices_into_the_local_path(tmp_path, monkeypatch):
    from imagen_cli.application import watermark_resample

    captured = {}

    def fake(repositories, store, artifact, **kwargs):
        captured.update(kwargs, artifact=artifact)
        return "succeeded", {"artifact": {"id": "art_x"}, "source": artifact,
                             "report": {}}

    monkeypatch.setattr(watermark_resample, "run", fake)
    with Container(workspace=str(tmp_path / "ws")) as container:
        status, _ = container.execute("watermark resample", {
            "artifact": ARTIFACT, "model": "realesrgan-x2plus", "factor": 0.25,
            "backend": "cuda", "tile": 512, "overlap": 8, "session": SESSION,
        })
    assert status == "succeeded"
    assert captured["artifact"] == ARTIFACT
    assert captured["model"] == "realesrgan-x2plus"
    assert captured["factor"] == 0.25
    assert captured["backend"] == "cuda"
    assert captured["tile"] == 512
    assert captured["overlap"] == 8
    assert captured["session"] == SESSION


def test_erase_carries_regions_to_a_remote(tmp_path, monkeypatch):
    captured = {}

    def fake(repositories, store, artifact, name, operation, **kwargs):
        captured.update(kwargs, operation=operation)
        return "approval_required", {"job_digest": "a" * 64}

    monkeypatch.setattr(remote_service, "run_operation", fake)
    with Container(workspace=str(tmp_path / "ws")) as container:
        container.execute("watermark erase", {
            "artifact": ARTIFACT, "remote": "modal", "regions": ["4,8,16,12"],
            "dilate": 5, "inpaint_method": "ns",
        })
    assert captured["operation"] == "watermark.erase"
    assert captured["params"]["regions"] == ["4,8,16,12"]
    assert captured["params"]["dilate"] == 5
    assert captured["params"]["inpaint_method"] == "ns"
