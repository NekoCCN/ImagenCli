"""Offline discovery through real CLI processes and persisted records."""

import pytest
from jsonschema import validate

from imagen_cli.application.container import Container
from imagen_cli.cli.registry import discover
from tests.integration.test_local_cli import invoke
from tests.unit.test_capabilities import capability


def seed(workspace):
    with Container(workspace=str(workspace)) as container:
        for identity, provider in (("p1", "openai"), ("p2", "gemini")):
            container.repositories.insert("profiles", {
                "id": identity, "name": identity, "provider": provider, "family": "images",
                "base_url": "https://example.test", "revision": 1,
                "model_default": "image-model", "credential_ref": "private-reference",
                "trust_policy": {},
            })
        for identity, model in (("one", "first"), ("two", "second")):
            container.repositories.insert("capabilities", capability(id=identity, model=model))


@pytest.mark.parametrize("command", ["providers", "models", "capabilities"])
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


def test_capability_discovery_retains_sources_and_evidence_without_promoting_status(tmp_path):
    workspace = tmp_path / "work"
    seed(workspace)
    code, result = invoke(workspace, "capabilities", "--profile=p1", "--model=second",
                          "--operation=generate")
    assert code == 0
    rows = result["data"]["items"]
    assert len(rows) == 1
    assert rows[0]["model"] == "second"
    assert rows[0]["support_status"] == "I"
    assert rows[0]["live_evidence"] == []
    # Public output retains source metadata but applies the shared URL redaction policy.
    assert rows[0]["sources"][0]["date"] == "2026-10-23"
    assert rows[0]["sources"][0]["locator"] == "fixture"
    assert rows[0]["sources"][0]["url"] == "[redacted]"


@pytest.mark.parametrize("command", ["providers", "models", "capabilities"])
def test_empty_discovery_is_truthful(tmp_path, command):
    code, result = invoke(tmp_path / "work", command)
    assert code == 0
    assert result["data"] == {"items": [], "next_cursor": None, "evidence": "offline_persisted"}


def test_missing_profile_does_not_list_unrelated_capabilities(tmp_path):
    workspace = tmp_path / "work"
    seed(workspace)
    code, result = invoke(workspace, "capabilities", "--profile=absent")
    assert code == 4
    assert result["data"] == {}
