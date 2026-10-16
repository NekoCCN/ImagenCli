"""Recover durable inline responses without submitting a second paid request."""

import base64

import pytest

from imagen_cli.application.container import Container
from imagen_cli.domain.primitive import new_id
from imagen_cli.providers.base import ArtifactDescriptor, ImmediateResult
from tests.integration.test_approved_execution import execute, service, setup


def _forbidden(*args, **kwargs):
    raise AssertionError("recovery must not create or resolve credentials")


@pytest.mark.parametrize("command", ["job fetch", "job reconcile"])
@pytest.mark.parametrize("damage", [None, "hash", "count", "url", "media"])
def test_saved_response_recovers_after_restart_without_create(
    tmp_path, png_bytes, monkeypatch, command, damage,
):
    workspace = str(tmp_path / "workspace")
    with Container(workspace=workspace) as container:
        plan, _, validator, executor, adapter, _ = setup(container, png_bytes)

        def create(request):
            adapter.requests.append(request)
            return ImmediateResult(
                1, 1, (ArtifactDescriptor(new_id("art"), 0, png_bytes),),
                private_response={"data": [{"b64_json": base64.b64encode(png_bytes).decode()}]},
            )

        monkeypatch.setattr(adapter, "create", create)

        def disk_full(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(executor.artifact_service, "save", disk_full)
        failed = execute(service(container, validator, executor), plan)
        assert failed["status"] == "save_failed"
        job_id = failed["job"]["id"]
        assert len(adapter.requests) == 1
        if damage:
            import json

            row = container.repositories.get("jobs", job_id, include_private=True)
            locator = json.loads(row["private_locator_ref"])
            private_path = container.private_state_store.root / locator["private_relpath"]
            if damage == "hash":
                private_path.write_bytes(b"tampered")
            else:
                body = {"count": {"data": []},
                        "url": {"data": [{"url": "http://127.0.0.1/private"}]},
                        "media": {"data": [{"b64_json": "bm90IGFuIGltYWdl"}]}}[damage]
                private_path.unlink()
                ref = container.private_state_store.write(job_id, json.dumps(body).encode())
                container.repositories.update(
                    "jobs", job_id, {"private_locator_ref": json.dumps(ref)},
                )

    monkeypatch.setattr("imagen_cli.execution.configured.ConfiguredAdapter.create", _forbidden)
    with Container(workspace=workspace) as container:
        status, data = container.execute(command, {"job_id": job_id})
        if damage:
            assert status == "save_failed"
            assert data["artifacts"] == []
            assert data["job"]["remote_state"] == "succeeded"
            assert container.repositories.get("sessions", plan.session_id)["version"] == 0
            return
        assert status == "succeeded"
        assert data["job"]["local_save_state"] == "saved"
        assert len(data["artifacts"]) == 1
        path = container.artifact_store.get(data["artifacts"][0]["id"])
        assert path.read_bytes() == png_bytes
        session = container.repositories.get("sessions", plan.session_id)
        assert session["current_turn_id"] == failed["turn"]["id"]
        assert session["version"] == 1
        assert container.execute(command, {"job_id": job_id}) == (status, data)
        assert len(container.repositories.list("artifacts")) == 1
        assert len(container.repositories.list("jobs")) == 1


def test_recovery_retains_staging_if_publication_fails(tmp_path, png_bytes, monkeypatch):
    with Container(workspace=str(tmp_path)) as container:
        plan, _, validator, executor, adapter, _ = setup(container, png_bytes)

        def create(request):
            return ImmediateResult(
                1, 1, (ArtifactDescriptor(new_id("art"), 0, png_bytes),),
                private_response={"data": [{"b64_json": base64.b64encode(png_bytes).decode()}]},
            )

        monkeypatch.setattr(adapter, "create", create)

        def disk_full(*args, **kwargs):
            raise OSError("disk full")

        with monkeypatch.context() as patch:
            patch.setattr(container.artifact_store, "stage", disk_full)
            result = execute(service(container, validator, executor), plan)
        with monkeypatch.context() as patch:
            patch.setattr(container.artifact_store, "publish", disk_full)
            status, _ = container.execute("job fetch", {"job_id": result["job"]["id"]})
            assert status == "save_failed"
        rows = container.repositories.list("artifacts", producer_job_id=result["job"]["id"])
        assert result["status"] == "save_failed"
        assert len(rows) == 1
        assert rows[0]["save_state"] == "staging"
        status, data = container.execute("job reconcile", {"job_id": result["job"]["id"]})
        assert status == "succeeded"
        assert len(data["artifacts"]) == 1
        assert data["artifacts"][0]["id"] == rows[0]["id"]


def test_url_output_recovers_through_the_safe_downloader(tmp_path, png_bytes, monkeypatch):
    workspace = str(tmp_path / "workspace")
    with Container(workspace=workspace) as container:
        plan, _, validator, executor, adapter, _ = setup(container, png_bytes)

        def create(request):
            return ImmediateResult(
                1, 1, (ArtifactDescriptor(new_id("art"), 0, png_bytes),),
                private_response={"data": [{"url": "https://cdn.example/out.png"}]},
            )

        monkeypatch.setattr(adapter, "create", create)

        def disk_full(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(executor.artifact_service, "save", disk_full)
        failed = execute(service(container, validator, executor), plan)
        assert failed["status"] == "save_failed"
        job_id = failed["job"]["id"]

    downloaded: list[str] = []
    monkeypatch.setattr(
        "imagen_cli.application.reconcile.fetch",
        lambda url, **kwargs: (downloaded.append(url), png_bytes)[1],
    )
    monkeypatch.setattr("imagen_cli.execution.configured.ConfiguredAdapter.create", _forbidden)
    with Container(workspace=workspace) as container:
        status, data = container.execute("job fetch", {"job_id": job_id})
        assert status == "succeeded"
        assert downloaded == ["https://cdn.example/out.png"]
        assert len(data["artifacts"]) == 1
        path = container.artifact_store.get(data["artifacts"][0]["id"])
        assert path.read_bytes() == png_bytes


def test_rejected_url_download_keeps_save_failed_without_create(tmp_path, png_bytes, monkeypatch):
    workspace = str(tmp_path / "workspace")
    with Container(workspace=workspace) as container:
        plan, _, validator, executor, adapter, _ = setup(container, png_bytes)

        def create(request):
            return ImmediateResult(
                1, 1, (ArtifactDescriptor(new_id("art"), 0, png_bytes),),
                private_response={"data": [{"url": "https://internal.example/out.png"}]},
            )

        monkeypatch.setattr(adapter, "create", create)

        def disk_full(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(executor.artifact_service, "save", disk_full)
        failed = execute(service(container, validator, executor), plan)
        job_id = failed["job"]["id"]

    def refuse(url, **kwargs):
        raise ValueError("host resolves outside the public internet")

    monkeypatch.setattr("imagen_cli.application.reconcile.fetch", refuse)
    monkeypatch.setattr("imagen_cli.execution.configured.ConfiguredAdapter.create", _forbidden)
    with Container(workspace=workspace) as container:
        status, data = container.execute("job fetch", {"job_id": job_id})
        assert status == "save_failed"
        assert data["artifacts"] == []
        assert data["job"]["remote_state"] == "succeeded"
