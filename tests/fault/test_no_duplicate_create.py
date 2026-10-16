"""No path here submits a second paid create for the same job."""

import pytest

from imagen_cli.application.container import Container
from imagen_cli.providers.contracts import ProviderContract, claims_exactly_once, contract
from tests.integration.test_approved_execution import execute, service, setup
from tests.integration.test_response_recovery import _forbidden


def test_unknown_idempotency_is_never_reported_as_exactly_once():
    for provider in ("mystery", "openai", "gemini"):
        value = contract(provider, "images" if provider != "gemini" else "generate_content")
        assert not claims_exactly_once(value)
    assert not claims_exactly_once(
        ProviderContract("x", "y", idempotency_scope="request_key")
    )


def test_unknown_outcome_is_preserved_and_never_retried(tmp_path, png_bytes):
    from imagen_cli.providers.base import ProviderFailure

    with Container(workspace=str(tmp_path)) as container:
        plan, _, validator, executor, adapter, _ = setup(
            container, png_bytes, error=ProviderFailure("outcome_unknown", accepted_maybe=True),
        )
        api = service(container, validator, executor)
        first = execute(api, plan)
        assert first["status"] == "unknown"
        assert first["job"]["remote_state"] == "unknown"
        assert len(adapter.requests) == 1
        assert execute(api, plan) == first
        assert len(adapter.requests) == 1


def test_replaying_a_consumed_plan_never_creates_again(tmp_path, png_bytes, monkeypatch):
    with Container(workspace=str(tmp_path)) as container:
        plan, _, validator, executor, adapter, _ = setup(container, png_bytes)
        api = service(container, validator, executor)
        result = execute(api, plan)
        assert result["status"] == "succeeded"
        monkeypatch.setattr(
            "imagen_cli.execution.configured.ConfiguredAdapter.create", _forbidden,
        )
        assert execute(api, plan) == result
        assert len(adapter.requests) == 1
        assert len(container.repositories.list("jobs")) == 1


@pytest.mark.parametrize("attempts", [1, 2])
def test_download_retries_never_touch_provider_create(attempts, tmp_path, png_bytes, monkeypatch):
    from imagen_cli.application.reconcile import recover_inline

    with Container(workspace=str(tmp_path)) as container:
        plan, _, validator, executor, adapter, _ = setup(container, png_bytes)

        calls = []

        def flaky(url, **kwargs):
            calls.append(url)
            raise ValueError("cdn unavailable")

        def disk_full(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(executor.artifact_service, "save", disk_full)
        from imagen_cli.domain.primitive import new_id
        from imagen_cli.providers.base import ArtifactDescriptor, ImmediateResult

        monkeypatch.setattr(adapter, "create", lambda request: ImmediateResult(
            1, 1, (ArtifactDescriptor(new_id("art"), 0, png_bytes),),
            private_response={"data": [{"url": "https://cdn.example/out.png"}]},
        ))
        result = execute(service(container, validator, executor), plan)
        job_id = result["job"]["id"]
        monkeypatch.setattr("imagen_cli.application.reconcile.fetch", flaky)
        monkeypatch.setattr(
            "imagen_cli.execution.configured.ConfiguredAdapter.create", _forbidden,
        )
        for _ in range(attempts):
            with pytest.raises(ValueError):
                recover_inline(executor.artifact_service, container.private_state_store, job_id)
        assert calls == ["https://cdn.example/out.png"] * attempts
