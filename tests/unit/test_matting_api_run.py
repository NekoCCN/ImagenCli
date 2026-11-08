"""Replicate matting: approval gate, parameter translation, publication rules."""

import io
from typing import Any

import pytest
from PIL import Image

from imagen_cli.application import matting_api
from imagen_cli.application.container import Container
from imagen_cli.application.session_service import SessionService
from imagen_cli.providers import replicate


def _png(size: tuple[int, int] = (32, 32), mode: str = "RGB",
         color: tuple[int, int, int] = (10, 20, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, color).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def published(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("REPLICATE_API_TOKEN", "r8_test")
    with Container(workspace=str(tmp_path / "workspace")) as container:
        service = SessionService(container.repositories, container.artifact_store)
        session = service.create_session("matting")
        turn = service.create_turn(
            session.id, instruction="source", operation="generate",
            resolved_profile_snapshot={}, model="fixture", parameters={},
        )
        artifact = service.save_artifact(
            turn.id, _png(), origin_manifest={"kind": "fixture"}
        )
        yield container, session.id, artifact.id


def _prediction(url: str = "https://delivery/out.png") -> replicate.Prediction:
    return replicate.Prediction(id="pred1", status="succeeded", output=(url,))


def _boom(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("the predictor must not be called before approval")


def _digest_for(container: Container, source: str,
                model: str = "sprited/birefnet-toonout",
                parameters: dict[str, Any] | None = None) -> str:
    row = container.repositories.get("artifacts", source)
    return matting_api.digest(row["sha256"], model, parameters or {})


def test_gate_blocks_without_accept_api(published):
    container, session, source = published
    status, data = matting_api.run(
        container.repositories, container.artifact_store, source,
        session=session, predictor=_boom,
    )
    assert status == "approval_required"
    assert data["api"]["uploads_to"] == "api.replicate.com"
    assert data["api"]["model"] == "sprited/birefnet-toonout"
    assert data["api"]["input_bytes"] > 0
    assert "--accept-api" in data["error"]["detail"]
    assert data["api_digest"] == _digest_for(container, source)


def test_wrong_digest_still_blocks(published):
    container, session, source = published
    status, _ = matting_api.run(
        container.repositories, container.artifact_store, source,
        session=session, accept_api="0" * 64, predictor=_boom,
    )
    assert status == "approval_required"


def test_success_publishes_rgba_and_records_manifest(published):
    container, session, source = published
    calls: dict[str, Any] = {}

    def fake(model: str, inputs: dict[str, Any], *, token: str,
             version: str | None = None, **kwargs: Any) -> replicate.Prediction:
        calls.update({"model": model, "inputs": inputs, "token": token,
                      "version": version})
        return _prediction()

    status, data = matting_api.run(
        container.repositories, container.artifact_store, source,
        session=session, accept_api=_digest_for(container, source),
        predictor=fake, fetch=lambda _url: _png(mode="RGBA"),
    )

    assert status == "succeeded"
    assert calls["model"] == "sprited/birefnet-toonout"
    assert str(calls["inputs"]["image"]).startswith("data:image/png;base64,")
    assert calls["token"] == "r8_test"
    report = data["report"]
    assert report["engine"]["backend"] == "api:replicate"
    assert report["api_job"] == "pred1"
    artifact = data["artifact"]
    assert artifact["id"] != source
    path = container.artifact_store.get(artifact["id"])
    with Image.open(path) as opened:
        assert opened.mode == "RGBA"
        assert opened.size == (32, 32)
    manifest = artifact["origin_manifest"]
    assert manifest["backend"] == "api:replicate"
    assert manifest["api_job"] == "pred1"
    assert manifest["source_sha256"] == container.repositories.get(
        "artifacts", source)["sha256"]
    assert manifest["verified"] is False


def test_parameters_are_translated_per_model(published):
    container, session, source = published
    seen: dict[str, Any] = {}

    def fake(model: str, inputs: dict[str, Any],
             **kwargs: Any) -> replicate.Prediction:
        seen["inputs"] = inputs
        return _prediction()

    matting_api.run(
        container.repositories, container.artifact_store, source, session=session,
        model="men1scus/birefnet", resolution=768,
        accept_api=_digest_for(container, source, "men1scus/birefnet",
                               {"resolution": 768}),
        predictor=fake, fetch=lambda _url: _png(),
    )
    assert seen["inputs"]["resolution"] == "768x768"
    assert str(seen["inputs"]["image"]).startswith("data:image/png;base64,")


def test_size_mismatch_fails_and_publishes_nothing(published):
    container, session, source = published
    before = len(container.repositories.list("artifacts"))
    status, data = matting_api.run(
        container.repositories, container.artifact_store, source, session=session,
        accept_api=_digest_for(container, source),
        predictor=lambda *a, **k: _prediction(),
        fetch=lambda _url: _png(size=(16, 16)),
    )
    assert status == "failed"
    assert "32" in data["error"]["detail"] and "16" in data["error"]["detail"]
    assert len(container.repositories.list("artifacts")) == before


def test_undecodable_output_fails(published):
    container, session, source = published
    status, _ = matting_api.run(
        container.repositories, container.artifact_store, source, session=session,
        accept_api=_digest_for(container, source),
        predictor=lambda *a, **k: _prediction(),
        fetch=lambda _url: b"not an image",
    )
    assert status == "failed"


def test_video_model_is_unsupported(published):
    container, session, source = published
    status, data = matting_api.run(
        container.repositories, container.artifact_store, source, session=session,
        model="sprited/birefnet-video", predictor=_boom,
    )
    assert status == "unsupported_capability"
    detail = data["error"]["detail"].lower()
    assert "video" in detail or "视频" in detail


def test_unknown_model_lists_the_valid_values(published):
    container, session, source = published
    status, data = matting_api.run(
        container.repositories, container.artifact_store, source, session=session,
        model="nobody/nothing", predictor=_boom,
    )
    assert status == "validation_error"
    assert "sprited/birefnet-toonout" in data["error"]["detail"]


def test_missing_key_is_unsupported(published, monkeypatch):
    container, session, source = published
    monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
    status, data = matting_api.run(
        container.repositories, container.artifact_store, source, session=session,
        accept_api=_digest_for(container, source), predictor=_boom,
    )
    assert status == "unsupported_capability"
    assert "REPLICATE_API_TOKEN" in data["error"]["detail"]


def test_unpublished_source_is_a_validation_error(published):
    container, session, _source = published
    status, _ = matting_api.run(
        container.repositories, container.artifact_store, "art_" + "0" * 32,
        session=session, predictor=_boom,
    )
    assert status == "validation_error"


def test_prediction_failure_maps_to_failed(published):
    container, session, source = published

    def failing(*_args: Any, **_kwargs: Any) -> Any:
        raise replicate.ReplicateError("timeout", "prediction never finished")

    status, data = matting_api.run(
        container.repositories, container.artifact_store, source, session=session,
        accept_api=_digest_for(container, source), predictor=failing,
    )
    assert status == "failed"
    assert data["error"]["type"] == "timeout"
