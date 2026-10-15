"""T037 handlers remain inert without an explicitly injected planning gate."""

from pathlib import Path
from typing import Any

import pytest

from imagen_cli.application.container import Container
from imagen_cli.application.profile_service import ProfileService
from imagen_cli.application.session_service import SessionService
from imagen_cli.capabilities.catalog import CapabilityCatalog
from imagen_cli.capabilities.validator import CapabilityValidator
from imagen_cli.cli.commands.create import AuthorizedPlan, CreateHandler
from imagen_cli.domain.contracts import CreationInput
from imagen_cli.domain.models import ProviderProfile


@pytest.fixture
def setup(tmp_path: Path) -> Any:
    with Container(workspace=str(tmp_path / "workspace")) as container:
        sessions = SessionService(container.repositories, container.artifact_store)
        session = sessions.create_session("poster")
        profile = ProviderProfile(
            id="offline",
            name="offline",
            provider="openai",
            family="images",
            base_url="https://api.example.invalid/v1",
            model_default="offline-model",
            credential_ref="unused",
            trust_policy={},
            revision=1,
        )
        record = dict(
            id="offline-cap",
            provider="openai",
            family="images",
            model="offline-model",
            model_revision="1",
            operation="generate",
            support_status="I",
            parameter_schema={
                "type": "object",
                "properties": {"count": {"type": "integer"}},
                "additionalProperties": False,
            },
            semantic_mapping={},
            input_limits={},
            transport_kind="sync",
            continuation_strategy="explicit_images",
            idempotency_contract={},
            cancel_contract={},
            sources=[
                {"url": "https://example.invalid", "date": "2026-10-23", "locator": "fixture"}
            ],
            checked_at="2026-10-23T00:00:00Z",
            offline_evidence=[],
            live_evidence=[],
            digest="a" * 64,
        )
        validator = CapabilityValidator(
            CapabilityCatalog([record, {**record, "id": "edit", "operation": "edit"}])
        )
        calls = []

        class Executor:
            def execute(self, request: Any, requested_count: int | None = None) -> dict[str, Any]:
                calls.append(request)
                assert not request.dispatch_intent_recorded
                return {"status": "succeeded", "artifacts": []}

        def build(gate: Any = None) -> CreateHandler:
            return CreateHandler(
                sessions, ProfileService(), validator, Executor(), planning_gate=gate
            )

        creation = CreationInput(
            schema_version=1, operation="generate", session=session.id, prompt="draw a cat"
        )
        yield container, sessions, session, profile, calls, build, creation


def test_missing_gate_async_and_capability_rejection_have_no_jobs(setup: Any) -> None:
    container, _, _, profile, calls, build, creation = setup
    assert build().handle(creation, profile=profile, model_revision="1")[0] == "approval_required"
    assert (
        build().handle(creation, profile=profile, model_revision="1", asynchronous=True)[0]
        == "unsupported_capability"
    )
    assert (
        build().handle(creation, profile=profile, model_revision="missing")[0]
        == "unsupported_capability"
    )
    assert calls == []
    assert container.repositories.list("turns") == []
    assert container.repositories.list("jobs") == []


def test_explicit_gate_executes_once_and_binds_profile_and_prompt(setup: Any) -> None:
    container, _, _, profile, calls, build, creation = setup
    seen = []

    def approve(snapshot: Any) -> AuthorizedPlan:
        seen.append(snapshot)
        assert snapshot.prompt == "draw a cat"
        assert snapshot.profile_identity == profile.id
        return AuthorizedPlan("pln_" + "1" * 32, snapshot.digest)

    status, data = build(approve).handle(creation, profile=profile, model_revision="1")
    assert status == "succeeded"
    assert len(calls) == len(seen) == 1
    assert calls[0].authorized is True
    assert calls[0].capability_digest == "a" * 64
    assert container.repositories.get("jobs", data["job_id"]) is not None


def test_changed_target_or_content_authorization_is_rejected(setup: Any) -> None:
    container, _, _, profile, calls, build, creation = setup
    status, _ = build(lambda snapshot: AuthorizedPlan("pln_" + "1" * 32, "b" * 64)).handle(
        creation, profile=profile, model_revision="1"
    )
    assert status == "approval_required"
    assert calls == []
    assert container.repositories.list("jobs") == []


def test_explicit_file_input_is_hashed_and_frozen_before_gate(
    setup: Any, tmp_path: Path, png_bytes: bytes
) -> None:
    _, _, _, profile, calls, build, creation = setup
    source = tmp_path / "input.png"
    source.write_bytes(png_bytes)

    def approve(snapshot: Any) -> AuthorizedPlan:
        import hashlib

        assert snapshot.input_hashes == (hashlib.sha256(png_bytes).hexdigest(),)
        source.write_bytes(b"changed after approval")
        return AuthorizedPlan("pln_" + "2" * 32, snapshot.digest)

    edited = creation.model_copy(update={"operation": "edit", "inputs": [f"file:{source}"]})
    status, _ = build(approve).handle(edited, profile=profile, model_revision="1")
    assert status == "succeeded"
    assert calls[0].inputs == (png_bytes,)


def test_invalid_selected_artifact_is_rejected_before_gate(setup: Any) -> None:
    container, _, _, profile, calls, build, creation = setup

    def forbidden(snapshot: Any) -> Any:
        pytest.fail("invalid selected artifact must not reach approval")

    edited = creation.model_copy(
        update={"operation": "edit", "inputs": ["artifact:art_" + "1" * 32]}
    )
    assert (
        build(forbidden).handle(edited, profile=profile, model_revision="1")[0]
        == "validation_error"
    )
    assert calls == []
    assert container.repositories.list("turns") == []
