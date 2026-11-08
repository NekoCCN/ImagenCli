"""Downscale-then-upscale removal: plan math, artifact rules, budget gate."""

import io

import pytest
from PIL import Image

from imagen_cli.application import watermark_resample
from imagen_cli.application.container import Container
from imagen_cli.application.session_service import SessionService


def _bytes(size, mode="RGB", color=(10, 20, 30), fmt="PNG"):
    buffer = io.BytesIO()
    Image.new(mode, size, color).save(buffer, format=fmt)
    return buffer.getvalue()


@pytest.fixture
def published(tmp_path):
    with Container(workspace=str(tmp_path / "workspace")) as container:
        service = SessionService(container.repositories, container.artifact_store)
        session = service.create_session("watermark")
        turn = service.create_turn(
            session.id, instruction="source", operation="generate",
            resolved_profile_snapshot={}, model="fixture", parameters={},
        )
        source = service.save_artifact(
            turn.id, _bytes((32, 32)), origin_manifest={"kind": "fixture"}
        )
        yield container, service, session.id, source


def _extra_artifact(service, session_id, data, instruction="extra"):
    turn = service.create_turn(
        session_id, instruction=instruction, operation="generate",
        resolved_profile_snapshot={}, model="fixture", parameters={},
    )
    return service.save_artifact(turn.id, data, origin_manifest={"kind": "fixture"})


def _doubler(array):
    return array.repeat(2, axis=0).repeat(2, axis=1)


def _gradient(size=(32, 32)):
    """A non-uniform image: a flat fill survives a round trip byte for byte."""
    image = Image.new("RGB", size)
    image.putdata([
        (x * 7 % 256, y * 5 % 256, (x + y) * 3 % 256)
        for y in range(size[1])
        for x in range(size[0])
    ])
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_plan_size_defaults_to_one_to_one():
    plan = watermark_resample.plan_size((640, 480), "realesrgan-x2plus", None)
    assert plan == {"input_size": [640, 480], "intermediate_size": [320, 240],
                    "output_size": [640, 480], "factor": 0.5, "scale": 2}


def test_plan_size_reports_a_net_change_when_factor_is_explicit():
    plan = watermark_resample.plan_size((640, 480), "realesrgan-x2plus", 0.3)
    assert plan["output_size"] == [384, 288]
    assert plan["output_size"] != plan["input_size"]


def test_plan_size_rounds_odd_sizes_down_to_the_factor():
    plan = watermark_resample.plan_size((513, 257), "realesrgan-x2plus", None)
    assert plan["output_size"] == [512, 256]
    assert plan["output_size"] != plan["input_size"]


def test_resample_rejects_an_unknown_model(published):
    container, _, _, source = published
    with pytest.raises(ValueError, match="super-resolution model"):
        watermark_resample.run(container.repositories, container.artifact_store,
                               source.id, model="nope")


def test_resample_rejects_an_unpublished_artifact(tmp_path):
    with Container(workspace=str(tmp_path / "w")) as container:
        with pytest.raises(ValueError, match="published artifact"):
            watermark_resample.run(container.repositories, container.artifact_store,
                                   "art_" + "0" * 32)


def test_resample_publishes_a_new_artifact_and_never_touches_the_source(published):
    container, service, session_id, _ = published
    source = _extra_artifact(service, session_id, _gradient())
    before = container.artifact_store.get(source.id).read_bytes()

    status, data = watermark_resample.run(
        container.repositories, container.artifact_store, source.id,
        model="realesrgan-x2plus", runner=_doubler,
    )

    assert status == "succeeded"
    produced = data["artifact"]
    assert produced["id"] != source.id
    assert produced["sha256"] != source.sha256
    assert (produced["width"], produced["height"]) == (32, 32)
    assert produced["origin_manifest"]["operation"] == "watermark.resample"
    assert produced["origin_manifest"]["verified"] is False
    assert container.artifact_store.get(source.id).read_bytes() == before


def test_resample_keeps_alpha_and_reports_odd_size(published):
    container, service, session_id, _ = published
    source = _extra_artifact(service, session_id, _bytes((17, 9), "RGBA", (1, 2, 3, 128)))

    _, data = watermark_resample.run(
        container.repositories, container.artifact_store, source.id,
        model="realesrgan-x2plus", runner=_doubler,
    )

    with Image.open(container.artifact_store.get(data["artifact"]["id"])) as opened:
        assert opened.mode == "RGBA"
        assert opened.size == (16, 8)
    assert data["report"]["plan"]["output_size"] == [16, 8]


def test_resample_rejects_unsupported_color_mode(published):
    container, service, session_id, _ = published
    source = _extra_artifact(
        service, session_id, _bytes((64, 64), "CMYK", fmt="JPEG"), "cmyk"
    )
    with pytest.raises(ValueError, match="color mode"):
        watermark_resample.run(container.repositories, container.artifact_store,
                               source.id, model="realesrgan-x2plus", runner=_doubler)


def test_resample_requires_the_budget_digest_when_too_large(published, monkeypatch):
    container, _, _, source = published
    from imagen_cli.application import postprocess_service

    monkeypatch.setattr(postprocess_service.settings, "memory_limit", lambda: 1)
    with pytest.raises(postprocess_service.BudgetApprovalRequired) as error:
        watermark_resample.run(container.repositories, container.artifact_store,
                               source.id, model="realesrgan-x2plus", runner=_doubler)
    digest = error.value.report["digest"]
    _, data = watermark_resample.run(
        container.repositories, container.artifact_store, source.id,
        model="realesrgan-x2plus", runner=_doubler, budget_approval=digest,
    )
    assert data["report"]["budget_approval"] == digest


@pytest.mark.cuda
def test_resample_runs_on_the_cuda_provider(published):
    container, _, _, source = published
    status, data = watermark_resample.run(
        container.repositories, container.artifact_store, source.id,
        model="realesrgan-x2plus", backend="cuda",
    )
    assert status == "succeeded"
    assert data["report"]["engine"]["provider"] == "CUDAExecutionProvider"
    plan = data["report"]["plan"]
    assert plan["output_size"] == plan["input_size"]
