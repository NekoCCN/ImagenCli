"""Layer 1: the cloud worker's watermark contract, exercised through real code.

``create_app`` is built without an ``execute`` override, so every request
travels HTTP -> ``_default_execute`` -> ``worker_cli.handle``. Only the
``remove_ai_watermarks`` package is faked, so what these tests pin is the
parameter translation and the publish/refuse decisions, not raiw's internals.
"""

import base64
import io
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytestmark = pytest.mark.filterwarnings(
    "ignore:Using `httpx` with `starlette.testclient` is deprecated"
)
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from deploy.worker.app import WorkerConfig, create_app  # noqa: E402
from imagen_cli.remote import worker_cli  # noqa: E402

AUTH = {"Authorization": "Bearer secret"}
DIGEST = "a" * 64


def _module(name: str) -> Any:
    """A stand-in module: assigning attributes to it is the whole point."""
    return types.ModuleType(name)


def _png(size: tuple[int, int] = (16, 16)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def _client(tmp_path: Any, mode: str = "single-replica-sync") -> TestClient:
    root = tmp_path / "jobs"
    root.mkdir(parents=True, exist_ok=True)
    config = WorkerConfig(
        token="secret", state_mode=mode, jobs_root=root,
        worker_version="1.2.3", imagen_cli_version="0.1.17",
        raiw_version="0.53.0", providers=("cuda",), gpu="L4",
    )
    return TestClient(create_app(config))


def _post(client, operation: str, *, params: dict[str, Any] | None = None,
          data: bytes | None = None) -> Any:
    payload = {
        "schema_version": 1, "operation": operation, "job_digest": DIGEST,
        "max_seconds": 30, "params": params or {},
    }
    return client.post(
        "/v1/jobs", headers=AUTH,
        data={"job": json.dumps(payload)},
        files={"input": ("in.png", data or _png(), "image/png")},
    )


class Mark:
    label = "logo"
    key = "logo"

    def __init__(self, detected: bool = True, confidence: float = 0.93) -> None:
        self.detected = detected
        self.confidence = confidence

    def detect(self, image: Any, *, provenance: bool = False) -> Any:
        return types.SimpleNamespace(detected=self.detected, confidence=self.confidence)

    def remove(self, image: Any, *, backend: str = "auto", provenance: bool = False,
               force: bool = False, detection: Any = None) -> tuple[Any, Any]:
        return object(), types.SimpleNamespace(backend=backend)


def _install(monkeypatch, *, record: dict[str, Any], visible_detected: bool = True,
             metadata_survivors: tuple[str, ...] = (), leftovers: tuple[str, ...] = (),
             classify_available: bool = True, raise_in: str | None = None) -> dict[str, Any]:
    """Install a fake ``remove_ai_watermarks`` package keyed by submodule name."""

    def guard(name: str) -> None:
        if raise_in == name:
            raise RuntimeError("boom")
        return None

    root = _module("remove_ai_watermarks")

    identify_mod = _module("remove_ai_watermarks.identify")

    def identify(source: Any) -> Any:
        guard("identify")
        report = types.SimpleNamespace(
            to_dict=lambda schema_version=1: {"platform": "google", "schema": schema_version}
        )
        return report

    identify_mod.identify = identify

    metadata_mod = _module("remove_ai_watermarks.metadata")

    def has_ai_metadata(source: Any) -> bool:
        guard("metadata")
        return True

    def strip_and_verify(source: Any, output: Any) -> tuple[Any, tuple[str, ...]]:
        guard("metadata")
        if not metadata_survivors:
            Image.new("RGB", (16, 16), (1, 2, 3)).save(output, format="PNG")
        return output, tuple(metadata_survivors)

    def strip_written_output(output: Any) -> tuple[str, ...]:
        return tuple(leftovers)

    metadata_mod.has_ai_metadata = has_ai_metadata
    metadata_mod.strip_and_verify = strip_and_verify
    metadata_mod.strip_written_output = strip_written_output

    image_io = _module("remove_ai_watermarks.image_io")

    def read_bgr_and_alpha(source: Any) -> tuple[Any, None]:
        guard("image_io")
        with Image.open(source) as opened:
            return opened.convert("RGB").copy(), None

    def write_bgr_with_alpha(output: Any, image: Any, alpha: Any) -> bool:
        guard("image_io")
        Image.new("RGB", (16, 16), (4, 5, 6)).save(output, format="PNG")
        return True

    image_io.read_bgr_and_alpha = read_bgr_and_alpha
    image_io.write_bgr_with_alpha = write_bgr_with_alpha
    image_io.write_bgr = lambda output, image: write_bgr_with_alpha(output, image, None)

    registry = _module("remove_ai_watermarks.watermark_registry")

    def get_mark(key: str) -> Mark:
        record["mark"] = key
        return Mark(detected=visible_detected)

    def resolve_trust(key: str, *, sensitivity: str = "auto", provenance: Any = None,
                      strict_keys: Any = ()) -> str:
        record["trust"] = (key, sensitivity)
        return "strict" if sensitivity == "strict" else "auto"

    registry.get_mark = get_mark
    registry.resolve_trust = resolve_trust

    eraser = _module("remove_ai_watermarks.region_eraser")

    def erase(image: Any, *, boxes: list[tuple[int, int, int, int]], backend: str = "cv2",
              dilate: int = 3, cv2_method: str = "telea") -> Any:
        guard("erase")
        record["erase"] = {"boxes": list(boxes), "backend": backend,
                           "dilate": dilate, "cv2_method": cv2_method}
        return object()

    eraser.erase = erase

    api = _module("remove_ai_watermarks.api")

    def visible_provenance(source: Any) -> dict[str, Any]:
        return {}

    class InvisibleOptions:
        def __init__(self, **kwargs: Any) -> None:
            record["invisible_options"] = kwargs

    def remove_visible_detailed(source: Any, output: Any, **kwargs: Any) -> Any:
        guard("visible")
        record["visible"] = kwargs
        Image.new("RGB", (16, 16), (7, 8, 9)).save(output, format="PNG")
        marks = [types.SimpleNamespace(label="logo", status="cleaned",
                                       confidence_before=0.9, confidence_after=0.1)]
        return types.SimpleNamespace(status="cleaned", marks=marks)

    def remove_all(source: Any, output: Any, *, backend: str = "auto",
                   sensitivity: str = "auto", invisible: Any = None, force: bool = False,
                   progress: Any = None) -> Any:
        guard("all")
        record["all"] = {"backend": backend, "sensitivity": sensitivity,
                         "invisible": invisible, "force": force}
        if progress is not None:
            progress("visible", "start")
            progress("invisible", "done")
        Image.new("RGB", (16, 16), (9, 9, 9)).save(output, format="PNG")
        return types.SimpleNamespace(
            visible_label="logo", visible_status="cleaned",
            visible_marks=[types.SimpleNamespace(label="logo", status="cleaned",
                                                 confidence_before=0.9,
                                                 confidence_after=0.1)],
            invisible={"status": "succeeded"},
        )

    root.remove_visible_detailed = remove_visible_detailed
    api.visible_provenance = visible_provenance
    api.InvisibleOptions = InvisibleOptions
    api.remove_visible_detailed = remove_visible_detailed
    api.remove_all = remove_all

    classify_mod = _module("remove_ai_watermarks.classify")

    def available() -> bool:
        return classify_available

    def classify_pixels(source: Any) -> Any:
        guard("classify")
        return types.SimpleNamespace(to_dict=lambda: {"pixels": "photo"})

    classify_mod.is_available = available
    classify_mod.classify_pixels = classify_pixels

    source_classify = _module("remove_ai_watermarks.source_classify")
    source_classify.is_available = available
    source_classify.classify_source = lambda source: types.SimpleNamespace(
        to_dict=lambda: {"source": "pipeline"}
    )

    engine_mod = _module("remove_ai_watermarks.invisible_engine")

    class InvisibleEngine:
        def __init__(self, *, pipeline: str, cpu_offload: bool = False) -> None:
            record["engine"] = {"pipeline": pipeline, "cpu_offload": cpu_offload}

        def remove_watermark(self, source: Any, output: Any, **options: Any) -> None:
            guard("invisible")
            record["invisible"] = options
            Image.new("RGB", (16, 16), (11, 11, 11)).save(output, format="PNG")

    engine_mod.InvisibleEngine = InvisibleEngine

    video = _module("remove_ai_watermarks.video")

    def _video_writer(name: str) -> Any:
        def write(source: Any, output: Any, **kwargs: Any) -> Any:
            guard("video")
            record[name] = kwargs
            Image.new("RGB", (16, 16), (12, 12, 12)).save(output, format="PNG")
            return types.SimpleNamespace(output=str(output), status="cleaned")
        return write

    video.identify_video = lambda source, check_visible=True: {"visible": bool(check_visible)}
    video.inspect_video_metadata = lambda source: {"had": True}
    video.remove_video_metadata = _video_writer("video_metadata")
    video.remove_video_visible = _video_writer("video_visible")
    video.remove_video_all = _video_writer("video_all")
    video.remove_video_invisible = _video_writer("video_invisible")
    video.remove_video_batch = lambda directory, output_directory, **kwargs: {"items": []}

    modules = {
        "remove_ai_watermarks": root,
        "remove_ai_watermarks.identify": identify_mod,
        "remove_ai_watermarks.metadata": metadata_mod,
        "remove_ai_watermarks.image_io": image_io,
        "remove_ai_watermarks.watermark_registry": registry,
        "remove_ai_watermarks.region_eraser": eraser,
        "remove_ai_watermarks.api": api,
        "remove_ai_watermarks.classify": classify_mod,
        "remove_ai_watermarks.source_classify": source_classify,
        "remove_ai_watermarks.invisible_engine": engine_mod,
        "remove_ai_watermarks.video": video,
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return record


def test_identify_reads_without_producing_an_output(tmp_path: Path, monkeypatch: Any) -> None:
    record = _install(monkeypatch, record={})
    client = _client(tmp_path)
    response = _post(client, "watermark.identify")
    assert response.status_code == 200
    data = response.json()
    assert data["report"] == {"platform": "google", "schema": 1}
    assert "output" not in data
    assert record.get("mark") is None


def test_metadata_refuses_to_publish_when_metadata_survives(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    _install(monkeypatch, record={}, metadata_survivors=("xmp",))
    client = _client(tmp_path)
    data = _post(client, "watermark.metadata").json()
    assert data["status"] == "failed"
    assert "survives" in data["error"]["message"]
    assert "output" not in data


def test_metadata_publishes_a_clean_copy(tmp_path: Path, monkeypatch: Any) -> None:
    _install(monkeypatch, record={})
    client = _client(tmp_path)
    data = _post(client, "watermark.metadata").json()
    assert data["status"] == "succeeded"
    assert data["report"]["had_ai_metadata"] is True
    assert data["output"]["media_type"] == "image/png"


def test_visible_forwards_backend_and_sensitivity(tmp_path: Path, monkeypatch: Any) -> None:
    record = _install(monkeypatch, record={})
    client = _client(tmp_path)
    _post(client, "watermark.visible",
          params={"backend": "lama", "sensitivity": "strict"})
    assert record["visible"] == {"backend": "lama", "sensitivity": "strict"}


def test_visible_named_mark_resolves_trust_and_cleans(tmp_path: Path, monkeypatch: Any) -> None:
    record = _install(monkeypatch, record={})
    client = _client(tmp_path)
    data = _post(client, "watermark.visible", params={"mark": "logo"}).json()
    assert data["status"] == "succeeded"
    assert record["mark"] == "logo"
    assert record["trust"] == ("logo", "auto")
    assert data["report"]["marks"][0]["status"] == "cleaned"


def test_visible_no_mark_reports_no_watermark(tmp_path: Path, monkeypatch: Any) -> None:
    record = _install(monkeypatch, record={}, visible_detected=False)
    client = _client(tmp_path)
    data = _post(client, "watermark.visible", params={"mark": "logo"}).json()
    assert data["report_status"] == "no_watermark"
    assert data["report"]["marks"] == []
    assert "output" in data  # a copy is still written, the pixels are unchanged
    assert record["mark"] == "logo"


def test_erase_translates_region_strings_into_int_boxes(tmp_path: Path, monkeypatch: Any) -> None:
    record = _install(monkeypatch, record={})
    client = _client(tmp_path)
    data = _post(client, "watermark.erase",
                 params={"regions": ["0,0,8,8", " 4, 4, 2, 2 "],
                         "dilate": 5, "inpaint_method": "ns"}).json()
    assert data["status"] == "succeeded"
    assert record["erase"]["boxes"] == [(0, 0, 8, 8), (4, 4, 2, 2)]
    assert record["erase"]["dilate"] == 5
    assert record["erase"]["cv2_method"] == "ns"


def test_classify_without_the_extra_explains_the_setup_command(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    _install(monkeypatch, record={}, classify_available=False)
    client = _client(tmp_path)
    data = _post(client, "watermark.classify").json()
    assert data["status"] == "failed"
    assert "setup --extras" in data["error"]["message"]


def test_classify_returns_pixels_report(tmp_path: Path, monkeypatch: Any) -> None:
    _install(monkeypatch, record={})
    client = _client(tmp_path)
    data = _post(client, "watermark.classify").json()
    assert data["report"] == {"pixels": "photo"}
    assert "output" not in data


def test_all_maps_the_invisible_options_and_records_stages(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    record = _install(monkeypatch, record={})
    client = _client(tmp_path, mode="shared-volume")
    _, job_id = _submit_async(client, "watermark.all", params={
        "pipeline": "sdxl-zimage", "backend": "cv2", "sensitivity": "auto",
        "tile": True, "tile_size": 512, "seed": 7,
    })
    data = _wait(client, job_id)
    assert data["status"] == "succeeded"
    assert record["invisible_options"]["pipeline"] == "sdxl-zimage"
    assert record["invisible_options"]["tile"] is True
    assert record["invisible_options"]["tile_size"] == 512
    assert record["invisible_options"]["seed"] == 7
    assert [stage["stage"] for stage in data["report"]["stages"]] == ["visible", "invisible"]
    assert data["report"]["visible_marks"][0]["label"] == "logo"


def test_invisible_uses_the_engine_with_the_requested_pipeline(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    record = _install(monkeypatch, record={})
    client = _client(tmp_path, mode="shared-volume")
    _, job_id = _submit_async(client, "watermark.invisible",
                              params={"pipeline": "sdxl-zimage", "cpu_offload": True})
    record_ = _wait(client, job_id)
    assert record_["status"] == "succeeded"
    assert record["engine"] == {"pipeline": "sdxl-zimage", "cpu_offload": True}


def test_a_raising_raiw_becomes_a_structured_failure(tmp_path: Path, monkeypatch: Any) -> None:
    _install(monkeypatch, record={}, raise_in="erase")
    client = _client(tmp_path)
    data = _post(client, "watermark.erase", params={"regions": ["0,0,4,4"]}).json()
    assert data["status"] == "failed"
    assert data["error"] == {"type": "RuntimeError", "message": "boom"}


def test_the_response_never_contains_a_traceback(tmp_path: Path, monkeypatch: Any) -> None:
    _install(monkeypatch, record={}, raise_in="identify")
    client = _client(tmp_path)
    body = _post(client, "watermark.identify").text
    assert "Traceback" not in body
    assert 'File "' not in body


def test_output_sha256_matches_the_bytes_the_worker_wrote(tmp_path: Path, monkeypatch: Any) -> None:
    _install(monkeypatch, record={})
    client = _client(tmp_path)
    data = _post(client, "watermark.metadata").json()
    written = base64.b64decode(data["output"]["base64"])
    import hashlib

    assert data["output"]["sha256"] == hashlib.sha256(written).hexdigest()
    assert written.startswith(b"\x89PNG")


def test_video_operations_map_to_their_own_raiw_functions(
    tmp_path: Path, monkeypatch: Any
) -> None:
    record = _install(monkeypatch, record={})
    source = tmp_path / "in.png"
    source.write_bytes(_png())
    output = tmp_path / "out.png"

    worker_cli.handle({
        "schema_version": 1, "operation": "watermark.video.visible", "input": str(source),
        "output": str(output), "params": {"mark": "logo", "backend": "cv2"},
    })
    assert record["video_visible"]["mark"] == "logo"

    worker_cli.handle({
        "schema_version": 1, "operation": "watermark.video.all", "input": str(source),
        "output": str(output), "params": {"include_invisible": True, "noise_std": 0.02},
    })
    assert record["video_all"]["include_invisible"] is True
    assert record["video_all"]["noise_std"] == 0.02

    worker_cli.handle({
        "schema_version": 1, "operation": "watermark.video.invisible",
        "input": str(source), "output": str(output),
        "params": {"model": "m", "device": "cuda"},
    })
    assert record["video_invisible"]["model"] == "m"
    assert record["video_invisible"]["device"] == "cuda"


def _submit_async(client: Any, operation: str,
                  *, params: dict[str, Any] | None = None) -> tuple[Any, str]:
    payload = {
        "schema_version": 1, "operation": operation, "job_digest": DIGEST,
        "max_seconds": 600, "params": params or {},
    }
    response = client.post(
        "/v1/jobs", headers=AUTH,
        data={"job": json.dumps(payload)},
        files={"input": ("in.png", _png(), "image/png")},
    )
    body = response.json()
    return response, body["job_id"]


def _wait(client, job_id: str) -> dict[str, Any]:
    import time

    record: dict[str, Any] = {}
    for _ in range(200):
        record = client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()
        if record["status"] in {"succeeded", "failed", "canceled"}:
            return record
        time.sleep(0.02)
    return record


def test_matte_reports_an_unsupported_precision_by_name(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import types

    from imagen_cli.application import matting_fp16

    fake_runtime = types.ModuleType("onnxruntime")
    fake_runtime.InferenceSession = object  # type: ignore[attr-defined]
    fake_runtime.get_available_providers = lambda: ["CPUExecutionProvider"]  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "onnxruntime", fake_runtime)

    def unsupported(model: str) -> None:
        raise matting_fp16.UnsupportedInThisImage("no onnxconverter-common here")

    monkeypatch.setattr(matting_fp16, "ensure_fp16", unsupported)
    source = tmp_path / "in.png"
    source.write_bytes(_png())
    response = worker_cli.handle({
        "schema_version": 1, "operation": "matte", "input": str(source),
        "output": str(tmp_path / "out.png"), "params": {"precision": "fp16"},
    })
    assert response["status"] == "failed"
    assert response["error"]["type"] == "unsupported_in_this_image"


def test_probe_reports_classify_false_when_its_weights_are_missing(
    monkeypatch: Any
) -> None:
    """A route existing is not a capability: the module says is_available() == False."""
    _install(monkeypatch, record={}, classify_available=False)
    probe = worker_cli.handle({"schema_version": 1, "operation": "probe"})
    assert probe["capabilities"]["classify"] is False
    assert probe["capabilities"]["source_classify"] is False


def test_probe_reports_classify_true_when_the_module_is_available(
    monkeypatch: Any
) -> None:
    _install(monkeypatch, record={}, classify_available=True)
    probe = worker_cli.handle({"schema_version": 1, "operation": "probe"})
    assert probe["capabilities"]["classify"] is True
