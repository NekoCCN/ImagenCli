"""Video artifacts: container sniffing, import limits, and command routing."""

import shutil
import subprocess

import pytest

from imagen_cli.application import watermark_service
from imagen_cli.application.container import Container
from imagen_cli.application.export_service import export_session
from imagen_cli.application.import_service import import_archive
from imagen_cli.application.session_service import SessionService
from imagen_cli.application.transfer_service import import_image
from imagen_cli.cli.entrypoint import _parse
from imagen_cli.storage.artifacts import inspect_media

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe are required for video fixtures",
)


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
         "color=c=red:s=32x24:d=0.2", "-pix_fmt", "yuv420p", "-y", str(path)],
        check=True,
    )
    return path


def test_inspect_media_reads_a_video_container(video):
    measured = inspect_media(video.read_bytes())
    assert measured["media_type"] == "video/mp4"
    assert (measured["width"], measured["height"]) == (32, 24)


def test_import_image_accepts_a_video(tmp_path, video):
    with Container(workspace=str(tmp_path / "ws")) as container:
        service = SessionService(container.repositories, container.artifact_store)
        session = service.create_session("video")
        data = import_image(container.repositories, container.artifact_store,
                            session.id, video)
        assert data["artifact"]["media_type"] == "video/mp4"
        assert data["artifact"]["width"] == 32
        assert container.artifact_store.get(data["artifact"]["id"]).suffix == ".image"


def test_video_and_image_commands_do_not_cross_media(video, tmp_path):
    with Container(workspace=str(tmp_path / "ws")) as container:
        service = SessionService(container.repositories, container.artifact_store)
        session = service.create_session("video")
        data = import_image(container.repositories, container.artifact_store,
                            session.id, video)
        with pytest.raises(ValueError, match="video"):
            watermark_service.run_operation(
                container.repositories, container.artifact_store,
                data["artifact"]["id"], "watermark.identify",
            )


def test_batch_refuses_an_output_directory_inside_the_source(tmp_path):
    source = tmp_path / "images"
    source.mkdir()
    with pytest.raises(ValueError, match="outside"):
        watermark_service.batch_directory(source, to=source / "clean")


def test_batch_forwards_mode_and_writes_to_the_given_directory(tmp_path, monkeypatch):
    source = tmp_path / "images"
    source.mkdir()
    target = tmp_path / "clean"
    captured = {}

    def fake_run(request, *, timeout):
        captured.update(request)
        return {"schema_version": 1, "status": "succeeded",
                "report": {"items": [{"source": "a.png"}], "succeeded": 1,
                           "failed": 0}}

    monkeypatch.setattr(watermark_service, "run", fake_run)
    data = watermark_service.batch_directory(
        source, to=target, mode="visible", accept_license=False,
    )
    assert captured["operation"] == "watermark.batch"
    assert captured["params"]["mode"] == "visible"
    assert data["output"] == str(target)


def test_video_commands_parse_their_tuning():
    command, args, _ = _parse([
        "watermark", "video", "invisible", "art_" + "a" * 32,
        "--noise-std", "0.15", "--fps", "24", "--device", "cpu",
    ])
    assert command == "watermark video invisible"
    assert args["noise_std"] == 0.15
    assert args["fps"] == 24.0
    command, args, _ = _parse([
        "watermark", "video", "batch", "/videos", "--mode", "visible",
        "--to", "/clean", "--include-invisible",
    ])
    assert command == "watermark video batch"
    assert args["directory"] == "/videos"
    assert args["include_invisible"] is True


def test_batch_refuses_a_non_empty_output_directory(tmp_path):
    source = tmp_path / "images"
    source.mkdir()
    target = tmp_path / "clean"
    target.mkdir()
    (target / "existing.png").write_bytes(b"x")
    with pytest.raises(ValueError, match="not empty"):
        watermark_service.batch_directory(source, to=target)


def test_a_video_roundtrips_through_a_session_archive(tmp_path, video):
    archive = tmp_path / "backup.zip"
    with Container(workspace=str(tmp_path / "a")) as first:
        service = SessionService(first.repositories, first.artifact_store)
        session = service.create_session("video")
        turn = service.create_turn(
            session.id, instruction="video", operation="generate",
            resolved_profile_snapshot={}, model="fixture", parameters={},
        )
        imported = service.save_artifact(
            turn.id, video.read_bytes(), origin_manifest={"kind": "fixture"},
        )
        export_session(first.repositories, first.artifact_store, session.id, archive)
    assert imported.media_type == "video/mp4"
    with Container(workspace=str(tmp_path / "b")) as second:
        import_archive(second.repositories, second.artifact_store, archive)
        art = next(row for row in second.repositories.list("artifacts")
                   if row["media_type"] == "video/mp4")
        assert (art["width"], art["height"]) == (32, 24)


def test_container_routes_both_batch_commands(tmp_path, monkeypatch):
    source = tmp_path / "media"
    source.mkdir()
    calls = []

    def fake_run(request, *, timeout):
        calls.append(request["operation"])
        return {"schema_version": 1, "status": "succeeded",
                "report": {"items": [{"source": "a"}], "succeeded": 1, "failed": 0}}

    monkeypatch.setattr(watermark_service, "run", fake_run)
    with Container(workspace=str(tmp_path / "ws")) as container:
        assert container.execute(
            "watermark batch", {"directory": str(source), "mode": "visible"},
        )[0] == "succeeded"
        assert container.execute(
            "watermark video batch", {"directory": str(source), "mode": "metadata"},
        )[0] == "succeeded"
    assert calls == ["watermark.batch", "watermark.video.batch"]
