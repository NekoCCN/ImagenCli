"""Unknown billing is not free, and provider usage is allowlisted."""

from datetime import date

import pytest

from imagen_cli.application.container import Container
from imagen_cli.application.cost_service import CostService
from imagen_cli.domain.models import Cost
from tests.integration.test_approved_execution import execute, service, setup


def test_missing_billing_and_usage_are_explicitly_unknown():
    value = CostService.normalize({})
    assert value.estimate is None
    assert value.actual is None
    assert value.currency is None
    assert value.usage is None
    assert value.unknown_reason


def test_usage_is_not_a_price_and_arbitrary_provider_fields_are_not_public():
    value = CostService.normalize({
        "input_tokens": 12, "output_tokens": 4, "total_tokens": 16,
        "promptTokenCount": 12, "secret": "fixture-secret", "cost": 0,
    })
    assert value.actual is None
    assert value.usage == {"input_tokens": 12, "output_tokens": 4, "total_tokens": 16,
                           "promptTokenCount": 12}
    assert "fixture-secret" not in value.model_dump_json()


def test_explicit_reported_zero_is_distinct_from_unknown():
    value = Cost(estimate=None, actual=0.0, currency="USD", billing_unit="request",
                 unknown_reason="no estimate", actual_source="provider response")
    assert value.actual == 0.0
    assert value.estimate is None


@pytest.mark.parametrize("usage", [None, [], {"total_tokens": True}, {"total_tokens": -1},
                                    {"total_tokens": float("nan")}])
def test_malformed_usage_does_not_invent_counts(usage):
    assert CostService.normalize(usage).usage is None


def test_estimate_requires_evidence():
    with pytest.raises(ValueError):
        Cost(estimate=1.0, actual=None, currency="USD", unknown_reason="no invoice")


def test_known_actual_cost_requires_a_verifiable_billing_source():
    with pytest.raises(ValueError):
        Cost(
            estimate=None, actual=0.01, currency="USD", billing_unit="request",
        )


def test_known_estimate_preserves_source_date_and_assumptions():
    value = Cost(
        estimate=0.01, actual=None, currency="USD", billing_unit="request",
        estimate_source="published tariff", estimate_date=date(2026, 10, 24),
        estimate_assumptions=["one image request"], unknown_reason="actual unavailable",
    )
    assert value.estimate == 0.01
    assert value.estimate_source == "published tariff"
    assert value.estimate_assumptions == ["one image request"]



@pytest.mark.parametrize("failure", ["unknown", "provider", "private_save"])
def test_failed_jobs_keep_explicit_unknown_billing(tmp_path, png_bytes, monkeypatch, failure):
    from imagen_cli.providers.base import ProviderFailure

    error = {"unknown": RuntimeError("uncertain"),
             "provider": ProviderFailure("provider_error"), "private_save": None}[failure]
    with Container(workspace=str(tmp_path)) as container:
        plan, _, validator, executor, adapter, _ = setup(container, png_bytes, error=error)
        if failure == "private_save":
            def disk_full(*args, **kwargs):
                raise OSError("disk full")

            monkeypatch.setattr(container.private_state_store, "write", disk_full)
        result = execute(service(container, validator, executor), plan)
        assert result["status"] == {
            "unknown": "unknown", "provider": "failed", "private_save": "save_failed",
        }[failure]
        assert result["job"]["cost"]["actual"] is None
        assert result["job"]["cost"]["estimate"] is None
        assert result["job"]["cost"]["unknown_reason"]
        assert len(adapter.requests) == 1


def test_executed_job_persists_unknown_cost_without_changing_creation(tmp_path, png_bytes):
    with Container(
        workspace=str(tmp_path),
    ) as container:
        plan, _, validator, executor, adapter, _ = setup(container, png_bytes)
        result = execute(service(container, validator, executor), plan)
        cost = result["job"]["cost"]
        assert cost["actual"] is None
        assert cost["estimate"] is None
        assert cost["usage"] is None
        assert cost["unknown_reason"]
        assert result["status"] == "succeeded"
        assert len(adapter.requests) == 1


def test_usage_counts_survive_redaction_but_secrets_do_not():
    from imagen_cli.cli.diagnostics import redact

    value = redact({"cost": {"usage": {"input_tokens": 12, "totalTokenCount": 16},
                             "api_key": "secret", "prompt": "hidden"}})
    assert value["cost"]["usage"] == {"input_tokens": 12, "totalTokenCount": 16}
    assert value["cost"]["api_key"] == "[redacted]"
    assert value["cost"]["prompt"] == "[redacted]"
