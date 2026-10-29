"""Worker failure, timeout and cancellation never return partial image outputs."""

import threading

import pytest
from PIL import Image

from imagen_cli.application import postprocess_worker


def test_worker_executes_real_pipeline(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (3, 4), (100, 120, 140)).save(source)
    data, metadata = postprocess_worker.execute(source, [{"op": "invert", "params": {}}])
    import io

    with Image.open(io.BytesIO(data)) as result:
        assert result.getpixel((0, 0)) == (155, 135, 115)
    assert metadata["execution"]["isolation"] == "spawn_process"
    assert metadata["execution"]["timeout_seconds"] == 120


def test_expired_deadline_terminates_worker(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (3, 4)).save(source)
    with pytest.raises(ValueError, match="timeout"):
        postprocess_worker.execute(source, [{"op": "invert", "params": {}}], timeout=0.001)


def test_preexisting_cancel_does_not_start_work(tmp_path):
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(ValueError, match="cancelled"):
        postprocess_worker.execute(tmp_path / "missing.png", [{"op": "invert"}],
                                   cancelled=cancelled)


def test_cancel_during_execution_terminates_worker(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (1000, 1000)).save(source)
    cancelled = threading.Event()
    timer = threading.Timer(0.05, cancelled.set)
    timer.start()
    try:
        with pytest.raises(ValueError, match="cancelled"):
            postprocess_worker.execute(source, [{"op": "rolling_guidance", "params": {
                "sigma_space": 16, "iterations": 10,
            }}], cancelled=cancelled)
    finally:
        timer.cancel()


def test_operation_error_is_reported_as_value_error(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (3, 4)).save(source)
    with pytest.raises(ValueError, match="radius"):
        postprocess_worker.execute(source, [{"op": "guided_smooth", "params": {"radius": 0}}])


def test_operation_error_message_is_plain(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (3, 4)).save(source)
    with pytest.raises(ValueError) as raised:
        postprocess_worker.execute(source, [{"op": "guided_smooth", "params": {"radius": 0}}])
    assert "ValueError:" not in str(raised.value)
    assert "radius" in str(raised.value)


@pytest.mark.parametrize(("pipeline", "expected"), [
    ([{"op": "superres", "params": {"model": "realesrgan-x4plus"}}],
     postprocess_worker.MODEL_RUNTIME_BYTES),
    ([{"op": "guided_smooth", "params": {}}], postprocess_worker.MAX_WORKING_BYTES),
    ([], postprocess_worker.MAX_WORKING_BYTES),
])
def test_model_pipelines_get_room_for_the_runtime(pipeline, expected):
    assert postprocess_worker._address_space_extra(pipeline) == expected


def test_allocation_failure_is_reported_in_plain_words():
    error = RuntimeError(
        "Fail: : 1 : FAIL : Non-zero status code returned while running Upsample node. "
        "Status Message: bfc_arena.cc: Failed to allocate memory for requested buffer "
        "of size 151519232"
    )
    message = postprocess_worker._error_message(error)
    assert message.startswith("not enough memory")
    assert "bfc_arena" not in message


def test_unexpected_error_keeps_its_class_name():
    assert postprocess_worker._error_message(RuntimeError("boom")).startswith("RuntimeError:")


def test_model_runs_get_a_longer_default_timeout():
    plain = [{"op": "guided_smooth", "params": {"radius": 4}}]
    model = [{"op": "superres", "params": {"model": "realesrgan-x4plus"}}]
    assert postprocess_worker.default_timeout(plain) == postprocess_worker.TRANSFORM_TIMEOUT
    assert postprocess_worker.default_timeout(model) == postprocess_worker.MODEL_TIMEOUT
