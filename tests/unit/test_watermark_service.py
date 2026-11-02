"""Watermark integration: estimate gate, isolated bridge contract, new-artifact rule."""

import io
import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from imagen_cli.application import watermark_service
from imagen_cli.application.container import Container
from imagen_cli.application.postprocess_service import BudgetApprovalRequired
from imagen_cli.application.session_service import SessionService
from imagen_cli.remote import worker_cli

GIB = 1024**3


def test_estimates_follow_pipeline_offload_and_tiling():
    assert watermark_service.estimate_bytes("qwen-zimage", 1024) == 64 * GIB + 1024 * 64
    assert watermark_service.estimate_bytes("sdxl-zimage", 1024) == 12 * GIB + 1024 * 64
    offloaded = watermark_service.estimate_bytes("qwen-zimage", 1024, cpu_offload=True)
    tiled = watermark_service.estimate_bytes("qwen-zimage", 1024, tile=True)
    assert offloaded < watermark_service.estimate_bytes("qwen-zimage", 1024)
    assert tiled > watermark_service.estimate_bytes("qwen-zimage", 1024)
    with pytest.raises(ValueError, match="pipeline"):
        watermark_service.estimate_bytes("nope", 1)


def test_over_limit_needs_the_exact_digest():
    with pytest.raises(BudgetApprovalRequired) as error:
        watermark_service.budget_preflight(
            "a" * 64, (2048, 2048), "qwen-zimage", memory_mb=1024,
        )
    digest = error.value.report["digest"]
    assert error.value.report["estimated_working_bytes"] > 1024 * 1024**2
    result = watermark_service.budget_preflight(
        "a" * 64, (2048, 2048), "qwen-zimage", memory_mb=1024, approval=digest,
    )
    assert result["budget_approval"] == digest


def test_memory_mb_overrides_the_configured_limit(monkeypatch):
    monkeypatch.setattr(watermark_service.settings, "memory_limit", lambda: 1)
    assert watermark_service.effective_limit() == 1
    assert watermark_service.effective_limit(4096) == 4096 * 1024**2


def test_the_bridge_runs_isolated_and_parses_json(monkeypatch):
    monkeypatch.setattr(watermark_service, "current_python", lambda: Path("/env/bin/python"))
    captured = {}

    def fake_run(command, **kwargs):
        captured.update(command=command, kwargs=kwargs)
        return subprocess.CompletedProcess(
            command, 0, json.dumps({"schema_version": 1, "status": "succeeded"}), "",
        )

    monkeypatch.setattr(watermark_service.subprocess, "run", fake_run)
    assert watermark_service.run({"operation": "probe"})["status"] == "succeeded"
    assert captured["command"][:3] == [str(Path("/env/bin/python")), "-I", "-m"]
    assert json.loads(captured["kwargs"]["input"])["schema_version"] == 1


def test_a_missing_environment_is_reported_not_crashed(monkeypatch):
    monkeypatch.setattr(watermark_service, "current_python", lambda: None)
    data = watermark_service.status()
    assert data["ready"] is False
    assert data["missing"] == ["interpreter"]


def test_worker_cli_rejects_unknown_contract_input():
    bad_schema = worker_cli.handle({"schema_version": 2, "operation": "probe"})
    assert bad_schema["status"] == "failed"
    unknown = worker_cli.handle({"schema_version": 1, "operation": "nope"})
    assert unknown["status"] == "failed"


def test_worker_cli_probe_without_raiw_reports_missing_capabilities():
    data = worker_cli.handle({"schema_version": 1, "operation": "probe"})
    assert data["status"] == "succeeded"
    assert data["capabilities"]["identify"] is False


@pytest.fixture
def published(tmp_path):
    with Container(workspace=str(tmp_path / "workspace")) as container:
        service = SessionService(container.repositories, container.artifact_store)
        session = service.create_session("watermark")
        turn = service.create_turn(
            session.id, instruction="source", operation="generate",
            resolved_profile_snapshot={}, model="fixture", parameters={},
        )
        buffer = io.BytesIO()
        Image.new("RGB", (32, 32), (10, 20, 30)).save(buffer, format="PNG")
        source = service.save_artifact(
            turn.id, buffer.getvalue(), origin_manifest={"kind": "fixture"}
        )
        yield container, service, source


def test_invisible_requires_explicit_license_acceptance(published, monkeypatch):
    container, _, source = published
    monkeypatch.setattr(
        watermark_service, "run", lambda *a, **k: pytest.fail("started without a license")
    )
    with pytest.raises(watermark_service.WatermarkError) as error:
        watermark_service.run_operation(
            container.repositories, container.artifact_store, source.id,
            "watermark.invisible",
        )
    assert error.value.status == "approval_required"
    assert error.value.data["license"]["pipeline"] == "qwen-zimage"
    assert "non-commercial" not in error.value.data["license"]["license"]


def test_invisible_over_budget_stops_before_the_subprocess(published, monkeypatch):
    container, _, source = published
    monkeypatch.setattr(
        watermark_service, "run", lambda *a, **k: pytest.fail("bridge started without consent")
    )
    with pytest.raises(BudgetApprovalRequired):
        watermark_service.run_operation(
            container.repositories, container.artifact_store, source.id,
            "watermark.invisible", memory_mb=1, accept_license=True,
        )


def test_identify_is_read_only_and_creates_no_artifact(published, monkeypatch):
    container, _, source = published
    monkeypatch.setattr(
        watermark_service, "run",
        lambda request, timeout=900: {
            "schema_version": 1, "status": "succeeded",
            "report": {"platform": "google"}, "raiw_version": "0.53.0",
        },
    )
    before = len(container.repositories.list("artifacts"))
    status, data = watermark_service.run_operation(
        container.repositories, container.artifact_store, source.id, "watermark.identify",
    )
    assert status == "succeeded"
    assert data["report"] == {"platform": "google"}
    assert len(container.repositories.list("artifacts")) == before


def test_a_second_local_gpu_run_is_refused(published, monkeypatch):
    container, _, source = published
    monkeypatch.setattr(
        watermark_service, "run", lambda *a, **k: pytest.fail("second run started")
    )
    assert watermark_service._LOCAL_RUN_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(watermark_service.WatermarkError, match="active"):
            watermark_service.run_operation(
                container.repositories, container.artifact_store, source.id,
                "watermark.visible",
            )
    finally:
        watermark_service._LOCAL_RUN_LOCK.release()


def test_visible_creates_a_new_artifact_and_keeps_the_source(published, monkeypatch):
    container, _, source = published

    def fake_run(request, *, timeout=900):
        output = Path(request["output"])
        Image.new("RGB", (32, 32), (9, 9, 9)).save(output, format="PNG")
        return {"schema_version": 1, "status": "cleaned", "report": {"status": "cleaned"},
                "raiw_version": "0.53.0"}

    monkeypatch.setattr(watermark_service, "run", fake_run)
    before = container.artifact_store.get(source.id).read_bytes()
    status, data = watermark_service.run_operation(
        container.repositories, container.artifact_store, source.id, "watermark.visible",
    )
    assert status == "succeeded"
    assert data["artifact"]["id"] != source.id
    assert container.artifact_store.get(source.id).read_bytes() == before
    assert data["artifact"]["origin_manifest"]["source"] == source.id


def test_update_previews_without_installing(monkeypatch, tmp_path):
    monkeypatch.setenv("IMAGEN_RAIW_ROOT", str(tmp_path))
    monkeypatch.setattr(watermark_service, "_imagen_version", lambda: "0.1.17")
    data = watermark_service.update(accept=False, to="0.53.1")
    assert data["installed"] is False
    assert "remove-ai-watermarks[visible]==0.53.1" in data["packages"]
    assert data["target"].endswith("raiw-0.53.1")
    with pytest.raises(ValueError, match="version"):
        watermark_service.update(to="not-a-version")

def test_erase_rejects_a_region_outside_the_image(published, monkeypatch):
    container, _, source = published
    monkeypatch.setattr(
        watermark_service, "run", lambda *a, **k: pytest.fail("started with a bad region")
    )
    with pytest.raises(ValueError, match="outside"):
        watermark_service.run_operation(
            container.repositories, container.artifact_store, source.id,
            "watermark.erase", regions=["16,16,64,64"],
        )


def test_erase_publishes_a_new_artifact(published, monkeypatch):
    container, _, source = published
    captured = {}

    def fake_run(request, *, timeout=900):
        captured.update(request)
        Image.new("RGB", (32, 32), (9, 9, 9)).save(request["output"], format="PNG")
        return {"schema_version": 1, "status": "succeeded", "raiw_version": "0.53.0"}

    monkeypatch.setattr(watermark_service, "run", fake_run)
    status, data = watermark_service.run_operation(
        container.repositories, container.artifact_store, source.id,
        "watermark.erase", regions=["1,1,4,4"], visible_backend="cv2",
    )
    assert status == "succeeded"
    assert data["artifact"]["id"] != source.id
    assert captured["operation"] == "watermark.erase"
    assert captured["params"]["regions"] == ["1,1,4,4"]


def test_all_requires_the_pipeline_license_and_budget(published, monkeypatch):
    container, _, source = published
    monkeypatch.setattr(
        watermark_service, "run", lambda *a, **k: pytest.fail("all started without consent")
    )
    with pytest.raises(watermark_service.WatermarkError) as error:
        watermark_service.run_operation(
            container.repositories, container.artifact_store, source.id,
            "watermark.all",
        )
    assert error.value.status == "approval_required"
    with pytest.raises(BudgetApprovalRequired):
        watermark_service.run_operation(
            container.repositories, container.artifact_store, source.id,
            "watermark.all", accept_license=True, memory_mb=1,
        )


def test_classify_is_read_only(published, monkeypatch):
    container, _, source = published
    monkeypatch.setattr(
        watermark_service, "run",
        lambda request, timeout=900: {
            "schema_version": 1, "status": "succeeded",
            "report": {"label": "google", "reason": "classified"},
        },
    )
    before = len(container.repositories.list("artifacts"))
    status, data = watermark_service.run_operation(
        container.repositories, container.artifact_store, source.id,
        "watermark.classify", options={"classify_source": True},
    )
    assert status == "succeeded"
    assert data["report"]["label"] == "google"
    assert len(container.repositories.list("artifacts")) == before

