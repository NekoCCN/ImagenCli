from contextlib import contextmanager
from dataclasses import FrozenInstanceError, replace
from types import MappingProxyType

import pytest

from imagen_cli.providers.base import (
    ArtifactDescriptor,
    ImmediateResult,
    NativeContextRef,
    ProviderFailure,
    ValidatedRequest,
)


def request() -> ValidatedRequest:
    return ValidatedRequest(
        plan_id="pln_1",
        job_id="job_1",
        profile_identity="profile",
        profile_revision=2,
        operation="generate",
        model="model",
        model_revision="rev",
        prompt="a prompt",
        normalized_parameters={"count": 1, "nested": {"safe": True}},
        capability_digest="digest",
        authorized=True,
        dispatch_intent_recorded=True,
        inputs=[b"input"],
        mask=b"mask",
        native_context=NativeContextRef("ctx", "a" * 64),
    )


def test_validated_request_is_frozen_and_snapshots_private_inputs_and_mapping() -> None:
    value = request()
    assert value.inputs == (b"input",)
    assert isinstance(value.normalized_parameters, MappingProxyType)
    with pytest.raises(TypeError):
        value.normalized_parameters["count"] = 2  # type: ignore[index]
    with pytest.raises(TypeError):
        value.normalized_parameters["nested"]["safe"] = False  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        value.prompt = "changed"  # type: ignore[misc]
    assert "input" not in repr(value)
    assert "mask" not in repr(value)


def test_native_context_ref_is_frozen() -> None:
    value = NativeContextRef("ctx", "a" * 64)
    assert value.identity == "ctx"
    with pytest.raises(FrozenInstanceError):
        value.identity = "other"  # type: ignore[misc]


def test_result_hides_content_and_private_response_from_repr() -> None:
    artifact = ArtifactDescriptor("art_1", 0, b"secret", "image/png")
    result = ImmediateResult(1, 1, (artifact,), {"latency_ms": 1}, {"raw": "secret"})
    assert "secret" not in repr(artifact)
    assert "secret" not in repr(result)
    assert isinstance(result.usage, MappingProxyType)
    with pytest.raises(TypeError):
        result.usage["new"] = 1  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        artifact.id = "other"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.actual_count = 0  # type: ignore[misc]


def test_mapping_snapshots_do_not_share_nested_mutable_values() -> None:
    source = {"nested": {"values": [1, 2]}}
    value = replace(request(), normalized_parameters=source)
    result = ImmediateResult(1, 0, [], source, source)
    source["nested"]["values"].append(3)
    assert value.normalized_parameters["nested"]["values"] == (1, 2)
    assert result.usage["nested"]["values"] == (1, 2)
    assert result.private_response["nested"]["values"] == (1, 2)
    assert result.artifacts == ()
    with pytest.raises(TypeError):
        result.private_response["nested"]["new"] = True


def test_provider_failure_exposes_only_safe_message_and_is_frozen() -> None:
    failure = ProviderFailure("provider_error", "safe detail", accepted_maybe=True)
    assert failure.type == "provider_error"
    assert failure.message == "safe detail"
    assert failure.accepted_maybe is True
    assert failure.retryable is False
    assert repr(failure) == (
        "ProviderFailure(type='provider_error', message='safe detail', accepted_maybe=True)"
    )
    with pytest.raises(FrozenInstanceError):
        failure.type = "other"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        del failure.accepted_maybe


def test_provider_failure_preserves_exception_traceback_protocol() -> None:
    @contextmanager
    def boundary():
        yield

    failure = ProviderFailure("outcome_unknown", accepted_maybe=True)
    with pytest.raises(ProviderFailure) as caught:
        with boundary():
            raise failure
    assert caught.value is failure
    assert failure.__traceback__ is not None
    assert ProviderFailure("provider_error").message == "provider_error"

