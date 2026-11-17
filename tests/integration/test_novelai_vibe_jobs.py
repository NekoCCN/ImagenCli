import pytest

from imagen_cli.application.container import Container
from imagen_cli.providers.base import ProviderFailure
from tests.unit.test_novelai_vibe_cache import Encoder, setup_vibe


@pytest.mark.parametrize("crash", [False, True])
def test_unknown_is_durable_and_consumed_is_observation_only(tmp_path, png_bytes, crash):
    class Failing(Encoder):
        def encode(self, request):
            self.calls.append(request)
            if crash:
                raise SystemExit("simulated process death")
            raise ProviderFailure("outcome_unknown", accepted_maybe=True)

    encoder = Failing()
    with Container(workspace=str(tmp_path)) as container:
        service, profile, identity, _ = setup_vibe(container, png_bytes, encoder)
        plan = service.plan(profile.id, identity, profile.model_default, 1)
        if crash:
            with pytest.raises(SystemExit):
                service.execute(plan["id"], plan["digest"])
        else:
            assert service.execute(plan["id"], plan["digest"])["state"] == "unknown"
    with Container(workspace=str(tmp_path)) as container:
        from imagen_cli.application.novelai_vibe_service import VibePreparationService

        service = VibePreparationService(
            container.repositories,
            container.artifact_store,
            container.private_state_store,
            adapter_factory=lambda _: encoder,
        )
        assert service.execute(plan["id"], plan["digest"])["state"] == "unknown"
        assert len(encoder.calls) == 1
        assert service.status(plan["id"])["accepted_maybe"] is True


def test_failed_preparation_never_becomes_generation_input(tmp_path, png_bytes):
    class Failing(Encoder):
        def encode(self, request):
            raise ProviderFailure("validation_error")

    with Container(workspace=str(tmp_path)) as container:
        service, profile, identity, _ = setup_vibe(container, png_bytes, Failing())
        plan = service.plan(profile.id, identity, profile.model_default, 1)
        assert service.execute(plan["id"], plan["digest"])["state"] == "failed"
        from imagen_cli.providers.base import ReferenceInput

        with pytest.raises(ValueError, match="vibe plan"):
            service.bind(profile, ReferenceInput(png_bytes, "vibe", {}), profile.model_default)


def test_generation_preflight_foreground_queue_bind_exact_private_encoding(
    tmp_path, png_bytes, monkeypatch
):
    import imagen_cli.application.preflight as preflight
    from imagen_cli.application.planning_service import PlanningService
    from imagen_cli.application.preflight import plan_creation
    from imagen_cli.domain.contracts import CreationInput
    from imagen_cli.execution.queued import build_request
    from tests.integration.test_approved_execution import service as execution_service
    from tests.integration.test_approved_execution import setup

    monkeypatch.setattr(preflight, "SUPPORTED_ROUTES", {("novelai", "generate_image")})
    with Container(workspace=str(tmp_path)) as container:
        _, _, validator, executor, adapter, _ = setup(container, png_bytes)
        vibe, profile, identity, encoder = setup_vibe(container, png_bytes)
        creation = CreationInput(
            schema_version=1,
            operation="generate",
            session="work",
            prompt="kite",
            profile=profile.id,
            model=profile.model_default,
            references=["artifact:" + identity],
            reference_options=[{"kind": "vibe", "settings": {"strength": 0.4}}],
        )
        args = (container.repositories, container.artifact_store, creation)
        status, _ = plan_creation(*args, private_store=container.private_state_store)
        assert status == "validation_error"
        assert not adapter.requests and not encoder.calls
        plan = vibe.plan(profile.id, identity, profile.model_default, 1)
        vibe.execute(plan["id"], plan["digest"])
        status, payload = plan_creation(*args, private_store=container.private_state_store)
        assert status == "approval_required"
        plan = payload["plan"]
        provenance = plan["portable_context"]["references"][0]["encoding"]
        assert "private_relpath" not in str(plan)
        assert "opaque-private-encoding" not in str(plan)
        PlanningService(container.repositories).approve(plan["id"], plan["plan_digest"])
        facts = execution_service(container, validator, executor).execute_approved(
            plan["id"],
            plan["plan_digest"],
            model_revision="local",
            reference_artifact_ids=(identity,),
            reference_options=({"kind": "vibe", "settings": {"strength": 0.4}},),
        )
        assert facts["status"] == "succeeded"
        import json

        from imagen_cli.application.export_service import build_manifest

        exported, assets = build_manifest(
            container.repositories, container.artifact_store, plan["session_id"]
        )
        assert "opaque-private-encoding" not in json.dumps(exported)
        assert all(b"opaque-private-encoding" not in value for value in assets.values())
        assert not any("vib_" in name for name in assets)
        recovered = build_request(
            container.repositories,
            container.artifact_store,
            facts["job"],
            private_store=container.private_state_store,
        )
        assert recovered.references == adapter.requests[0].references
        assert recovered.references[0].prepared_content == b"opaque-private-encoding"
        assert (
            dict(recovered.references[0].encoding)["preparation_id"] == provenance["preparation_id"]
        )
        assert len(encoder.calls) == 1
        row = container.repositories.get("vibe_preparations", provenance["preparation_id"])
        path = container.private_state_store.root / (row["id"] + ".bin")
        path.write_bytes(b"changed")
        with pytest.raises(ValueError, match="hash"):
            build_request(
                container.repositories,
                container.artifact_store,
                facts["job"],
                private_store=container.private_state_store,
            )


def test_vibe_cli_schema_parse_and_status_are_offline(tmp_path, png_bytes):
    from imagen_cli.cli.entrypoint import _parse as parse

    with Container(workspace=str(tmp_path)) as container:
        service, profile, identity, encoder = setup_vibe(container, png_bytes)
        command, args, _ = parse(
            [
                "vibe",
                "plan",
                identity,
                "--profile",
                profile.id,
                "--model",
                profile.model_default,
                "--information-extracted",
                "0.5",
            ]
        )
        status, payload = container.execute(command, args)
        assert status == "planned"
        row = payload["preparation"]
        command, args, _ = parse(["vibe", "status", row["id"]])
        assert container.execute(command, args)[1]["preparation"]["state"] == "planned"
        assert not encoder.calls


def test_competing_execute_observes_committed_intent_without_resubmitting(tmp_path, png_bytes):
    from imagen_cli.application.novelai_vibe_service import VibePreparationService

    class Observing(Encoder):
        def encode(self, request):
            self.calls.append(request)
            with Container(workspace=str(tmp_path)) as other:
                peer = VibePreparationService(
                    other.repositories,
                    other.artifact_store,
                    other.private_state_store,
                    adapter_factory=lambda _: self,
                )
                assert peer.execute(plan["id"], plan["digest"])["state"] == "unknown"
            return b"opaque-private-encoding"

    encoder = Observing()
    with Container(workspace=str(tmp_path)) as container:
        service, profile, identity, _ = setup_vibe(container, png_bytes, encoder)
        plan = service.plan(profile.id, identity, profile.model_default, 1)
        assert service.execute(plan["id"], plan["digest"])["state"] == "succeeded"
        assert len(encoder.calls) == 1


def test_async_manifest_binds_provenance_and_no_automatic_preparation(
    tmp_path, png_bytes, monkeypatch
):
    import imagen_cli.application.preflight as preflight
    from imagen_cli.application.preflight import plan_creation
    from imagen_cli.application.session_service import SessionService
    from imagen_cli.domain.contracts import CreationInput
    from imagen_cli.execution.queued import build_request

    monkeypatch.setattr(preflight, "SUPPORTED_ROUTES", {("novelai", "generate_image")})
    with Container(workspace=str(tmp_path)) as container:
        SessionService(container.repositories, container.artifact_store).create_session("work")
        service, profile, identity, encoder = setup_vibe(container, png_bytes)
        creation = CreationInput(
            schema_version=1,
            operation="generate",
            session="work",
            prompt="kite",
            profile=profile.id,
            model=profile.model_default,
            references=["artifact:" + identity],
            reference_options=[{"kind": "vibe", "settings": {}}],
        )
        plan = service.plan(profile.id, identity, profile.model_default, 1)
        service.execute(plan["id"], plan["digest"])
        status, payload = plan_creation(
            container.repositories,
            container.artifact_store,
            creation,
            asynchronous=True,
            private_store=container.private_state_store,
        )
        assert status == "accepted"
        assert payload["turn"]["input_manifest"][0]["encoding"]["preparation_id"] == plan["id"]
        request = build_request(
            container.repositories,
            container.artifact_store,
            payload["job"],
            private_store=container.private_state_store,
        )
        assert request.references[0].prepared_content == b"opaque-private-encoding"
        assert len(encoder.calls) == 1


def test_cache_audit_never_classifies_preparation_as_orphan(tmp_path, png_bytes):
    from imagen_cli.application.cache_service import audit

    with Container(workspace=str(tmp_path)) as container:
        service, profile, identity, _ = setup_vibe(container, png_bytes)
        plan = service.plan(profile.id, identity, profile.model_default, 1)
        service.execute(plan["id"], plan["digest"])
        entries = audit(
            container.repositories,
            container.artifact_store,
            private_store=container.private_state_store,
        )
        assert not any(entry.identity == plan["id"] for entry in entries)


def test_fresh_plan_after_unknown_discloses_duplicate_charge_in_digest(tmp_path, png_bytes):
    class Failing(Encoder):
        def encode(self, request):
            raise ProviderFailure("outcome_unknown", accepted_maybe=True)

    with Container(workspace=str(tmp_path)) as container:
        service, profile, identity, _ = setup_vibe(container, png_bytes, Failing())
        first = service.plan(profile.id, identity, profile.model_default, 1)
        service.execute(first["id"], first["digest"])
        second = service.plan(profile.id, identity, profile.model_default, 1)
        assert second["plan"]["prior_unknown_preparation_id"] == first["id"]
        assert "another possibly charged request" in second["plan"]["duplicate_cost_warning"]
        assert second["digest"] != first["digest"]


@pytest.mark.parametrize("first_outcome", ["success", "unknown"])
def test_distinct_stale_plan_reconciles_intervening_outcome(tmp_path, png_bytes, first_outcome):
    class First(Encoder):
        def encode(self, request):
            self.calls.append(request)
            if first_outcome == "unknown":
                raise ProviderFailure("outcome_unknown", accepted_maybe=True)
            return b"opaque-private-encoding"

    encoder = First()
    with Container(workspace=str(tmp_path)) as container:
        service, profile, identity, _ = setup_vibe(container, png_bytes, encoder)
        first = service.plan(profile.id, identity, profile.model_default, 1)
        stale = service.plan(profile.id, identity, profile.model_default, 1)
        service.execute(first["id"], first["digest"])
        if first_outcome == "success":
            result = service.execute(stale["id"], stale["digest"])
            assert result["state"] == "succeeded"
            assert result["reused_preparation_id"] == first["id"]
        else:
            with pytest.raises(ValueError, match="duplicate.*replan"):
                service.execute(stale["id"], stale["digest"])
            assert service.status(stale["id"])["consumed"] == 0
            renewed = service.plan(profile.id, identity, profile.model_default, 1)
            assert first["id"] in renewed["plan"]["prior_unknown_preparation_ids"]
            assert renewed["plan"]["duplicate_cost_warning"]
        assert len(encoder.calls) == 1


@pytest.mark.parametrize("first_outcome", ["success", "unknown"])
def test_interleaved_distinct_plan_cannot_dispatch_during_first_attempt(
    tmp_path,
    png_bytes,
    first_outcome,
):
    from imagen_cli.application.novelai_vibe_service import VibePreparationService

    class Interleaving(Encoder):
        def encode(self, request):
            self.calls.append(request)
            with Container(workspace=str(tmp_path)) as other:
                peer = VibePreparationService(
                    other.repositories,
                    other.artifact_store,
                    other.private_state_store,
                    adapter_factory=lambda _: self,
                )
                with pytest.raises(ValueError, match="duplicate.*replan"):
                    peer.execute(stale["id"], stale["digest"])
            if first_outcome == "unknown":
                raise ProviderFailure("outcome_unknown", accepted_maybe=True)
            return b"opaque-private-encoding"

    encoder = Interleaving()
    with Container(workspace=str(tmp_path)) as container:
        service, profile, identity, _ = setup_vibe(container, png_bytes, encoder)
        first = service.plan(profile.id, identity, profile.model_default, 1)
        stale = service.plan(profile.id, identity, profile.model_default, 1)
        state = service.execute(first["id"], first["digest"])["state"]
        assert state == ("succeeded" if first_outcome == "success" else "unknown")
        if first_outcome == "success":
            assert service.execute(stale["id"], stale["digest"])["state"] == "succeeded"
        else:
            with pytest.raises(ValueError, match="duplicate.*replan"):
                service.execute(stale["id"], stale["digest"])
        assert len(encoder.calls) == 1


def test_duplicate_warning_approval_is_invalidated_by_another_unknown_attempt(tmp_path, png_bytes):
    class Unknown(Encoder):
        def encode(self, request):
            self.calls.append(request)
            raise ProviderFailure("outcome_unknown", accepted_maybe=True)

    encoder = Unknown()
    with Container(workspace=str(tmp_path)) as container:
        service, profile, identity, _ = setup_vibe(container, png_bytes, encoder)
        first = service.plan(profile.id, identity, profile.model_default, 1)
        service.execute(first["id"], first["digest"])
        acknowledged = service.plan(profile.id, identity, profile.model_default, 1)
        stale_acknowledged = service.plan(profile.id, identity, profile.model_default, 1)
        assert acknowledged["plan"]["prior_unknown_preparation_ids"] == [first["id"]]
        assert service.execute(acknowledged["id"], acknowledged["digest"])["state"] == "unknown"
        with pytest.raises(ValueError, match="duplicate.*replan"):
            service.execute(stale_acknowledged["id"], stale_acknowledged["digest"])
        assert len(encoder.calls) == 2
        refreshed = service.plan(profile.id, identity, profile.model_default, 1)
        assert set(refreshed["plan"]["prior_unknown_preparation_ids"]) == {
            first["id"],
            acknowledged["id"],
        }


def test_intervening_success_corruption_cannot_fall_back_to_paid_dispatch(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path)) as container:
        service, profile, identity, encoder = setup_vibe(container, png_bytes)
        first = service.plan(profile.id, identity, profile.model_default, 1)
        stale = service.plan(profile.id, identity, profile.model_default, 1)
        service.execute(first["id"], first["digest"])
        (container.private_state_store.root / (first["id"] + ".bin")).write_bytes(b"corrupt")
        with pytest.raises(ValueError, match="hash"):
            service.execute(stale["id"], stale["digest"])
        assert service.status(stale["id"])["consumed"] == 0
        assert len(encoder.calls) == 1
