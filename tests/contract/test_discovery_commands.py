"""Offline discovery through real CLI processes and persisted records."""

import pytest
from jsonschema import validate

from imagen_cli.application.container import Container
from imagen_cli.cli.registry import discover
from tests.integration.test_local_cli import invoke


def seed(workspace):
    with Container(workspace=str(workspace)) as container:
        for identity, provider in (("p1", "openai"), ("p2", "gemini")):
            container.repositories.insert("profiles", {
                "id": identity, "name": identity, "provider": provider, "family": "images",
                "base_url": "https://example.test", "revision": 1,
                "model_default": "image-model", "credential_ref": "private-reference",
                "trust_policy": {},
            })
        for profile, model in (("p1", "first"), ("p2", "second")):
            container.repositories.insert("provider_models", {
                "id": f"{profile}:{model}", "profile_id": profile, "model_id": model,
                "display_name": model, "fetched_at": "2026-10-24T00:00:00Z",
                "source_url": "", "evidence": "manual",
            })


@pytest.mark.parametrize("command", ["providers", "models"])
def test_discovery_pages_persisted_facts_and_matches_schema(tmp_path, command):
    workspace = tmp_path / "work"
    seed(workspace)
    code, result = invoke(workspace, command, "--limit=1")
    assert code == 0
    data = result["data"]
    assert len(data["items"]) == 1
    assert data["evidence"] == "offline_persisted"
    assert data["next_cursor"] == "offset_1"
    assert "private-reference" not in str(data)
    code, following = invoke(workspace, command, "--limit=1", "--cursor=offset_1")
    assert code == 0
    assert following["data"]["next_cursor"] is None
    assert following["data"]["items"] != data["items"]
    assert discover(command)["commands"][0]["available"] is True
    validate(result, discover(command)["output_schema"])


def test_model_discovery_filters_by_profile_and_model(tmp_path):
    workspace = tmp_path / "work"
    seed(workspace)
    code, result = invoke(workspace, "models", "--profile=p1", "--model=first")
    assert code == 0
    rows = result["data"]["items"]
    assert [(row["profile"], row["model"]) for row in rows] == [("p1", "first")]
    assert rows[0]["provider"] == "openai"


@pytest.mark.parametrize("command", ["providers", "models"])
def test_empty_discovery_is_truthful(tmp_path, command):
    code, result = invoke(tmp_path / "work", command)
    assert code == 0
    assert result["data"] == {"items": [], "next_cursor": None, "evidence": "offline_persisted"}


def test_missing_profile_does_not_list_unrelated_models(tmp_path):
    workspace = tmp_path / "work"
    seed(workspace)
    code, result = invoke(workspace, "models", "--profile=absent")
    assert code == 4
    assert result["data"] == {}
