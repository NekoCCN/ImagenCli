"""Delivered command discovery must describe actual local CLI results."""

import pytest
from jsonschema import validate

from imagen_cli.cli.registry import discover
from tests.integration.test_local_cli import invoke, seed_job


@pytest.mark.parametrize("operation", ["create", "list", "show", "resume"])
def test_session_discovery_matches_delivered_output(tmp_path, operation):
    workspace = tmp_path / "work"
    _, created = invoke(workspace, "session", "create", "sample")
    identity = created["data"]["session"]["id"]
    args = {"create": ("another",), "list": (), "show": (identity,), "resume": (identity,)}
    code, value = invoke(workspace, "session", operation, *args[operation])
    assert code == 0
    discovery = discover("session " + operation)
    assert discovery["commands"][0]["available"] is True
    validate(value, discovery["output_schema"])


@pytest.mark.parametrize("operation", ["get", "wait", "fetch", "reconcile"])
def test_job_discovery_matches_delivered_output(tmp_path, operation):
    workspace = tmp_path / "work"
    identity = seed_job(workspace, staging=True)
    if operation == "fetch":
        invoke(workspace, "job", "reconcile", identity)
    _, value = invoke(workspace, "job", operation, identity)
    discovery = discover("job " + operation)
    assert discovery["commands"][0]["available"] is True
    validate(value, discovery["output_schema"])


def test_missing_session_error_matches_advertised_output(tmp_path):
    code, value = invoke(tmp_path / "work", "session", "show", "missing")
    assert code == 4
    validate(value, discover("session show")["output_schema"])


def test_delivered_schema_rejects_missing_session_data():
    from jsonschema import ValidationError

    with pytest.raises(ValidationError):
        validate({}, discover("session show")["data_schema"])
