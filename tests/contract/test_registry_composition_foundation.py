"""T020/T026 contracts: discovery is descriptive, never execution authorization."""

import io
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from imagen_cli.application.container import Container
from imagen_cli.cli.entrypoint import main
from imagen_cli.cli.registry import COMMANDS, discover


def test_every_command_has_strict_input_and_data_discovery() -> None:
    for name in COMMANDS:
        data = discover(name)
        for key in ("input_schema", "data_schema", "output_schema"):
            schema = data[key]
            assert schema.get("type") == "object", (name, key)
            jsonschema.Draft202012Validator.check_schema(schema)
        assert data["input_schema"]["additionalProperties"] is False
        assert not jsonschema.Draft202012Validator(data["input_schema"]).is_valid(
            {"invented_parameter": True}
        )
        output_data = data["output_schema"]["properties"]["data"]
        if COMMANDS[name].available and name not in {"help", "schema"}:
            assert data["data_schema"] in output_data["anyOf"]
        else:
            assert output_data == data["data_schema"]
        assert data["commands"][0]["input_schema"] == data["input_schema"]
        assert data["commands"][0]["data_schema"] == data["data_schema"]


def test_creation_discovery_is_operation_specific() -> None:
    generate = jsonschema.Draft202012Validator(discover("generate")["input_schema"])
    edit = jsonschema.Draft202012Validator(discover("edit")["input_schema"])
    request: dict[str, Any] = {
        "schema_version": 1,
        "operation": "generate",
        "session": "poster",
        "prompt": "海报",
    }
    assert generate.is_valid(request)
    assert not edit.is_valid(request)
    request["operation"] = "edit"
    assert not generate.is_valid(request)
    assert not edit.is_valid(request)
    request["inputs"] = ["file:portrait.png"]
    assert edit.is_valid(request)
    request["profile"] = None
    assert not edit.is_valid(request)
    del request["profile"]
    request["retry_of_job_id"] = "job_" + "1" * 32
    assert not edit.is_valid(request)
    request["ack_duplicate_cost"] = False
    assert not edit.is_valid(request)
    request["ack_duplicate_cost"] = True
    assert edit.is_valid(request)


@pytest.mark.parametrize(
    "name,valid,invalid",
    [
        ("session create", {"name": "海报"}, {}),
        ("job wait", {"job_id": "job_" + "1" * 32}, {"job_id": "wrong"}),
        ("job cancel", {"job_id": "job_" + "1" * 32, "scope": "wait"},
         {"job_id": "job_" + "1" * 32, "scope": "all"}),
        ("plan approve", {"plan_id": "pln_" + "1" * 32, "digest": "a" * 64}, {}),
        ("batch submit", {"requests": [], "concurrency": 2},
         {"requests": [], "concurrency": 9}),
    ],
)
def test_command_specific_parameters(
    name: str, valid: dict[str, Any], invalid: dict[str, Any]
) -> None:
    # Batch count is independently covered below; an empty batch is never valid.
    if name == "batch submit":
        request = {"schema_version": 1, "operation": "generate", "session": "s", "prompt": "p"}
        valid["requests"] = [request]
        invalid["requests"] = [request]
    validator = jsonschema.Draft202012Validator(discover(name)["input_schema"])
    assert validator.is_valid(valid)
    assert not validator.is_valid(invalid)


def test_batch_and_pagination_limits_are_discoverable() -> None:
    batch = jsonschema.Draft202012Validator(discover("batch submit")["input_schema"])
    assert not batch.is_valid({"requests": []})
    listing = jsonschema.Draft202012Validator(discover("session list")["input_schema"])
    assert listing.is_valid({})
    assert not listing.is_valid({"limit": 1001})
    assert not listing.is_valid({"limit": True})
    assert not listing.is_valid({"cursor": "bad!"})


def test_discovery_is_a_fresh_snapshot() -> None:
    first = discover("generate")
    first["input_schema"]["properties"].clear()
    first["options"]["timeout"]["default"] = -1
    assert "prompt" in discover("generate")["input_schema"]["properties"]
    assert discover()["options"]["timeout"]["default"] == 300


def test_catalog_and_machine_results_validate_the_advertised_data(tmp_path: Path) -> None:
    for name in (None, *COMMANDS):
        out = io.StringIO()
        args = ["schema", *([] if name is None else name.split()), "--agent"]
        assert main(args, stdout=out, stderr=io.StringIO()) == 0
        response = json.loads(out.getvalue())
        assert len(response["data"]["commands"]) == (len(COMMANDS) if name is None else 1)
        jsonschema.validate(response["data"], discover("schema")["data_schema"])
        if name is not None and not COMMANDS[name].available:
            status, payload = Container(workspace=str(tmp_path / "unused")).execute(name)
            assert status == "unsupported_capability"
            assert payload == {}
            jsonschema.validate(payload, discover(name)["data_schema"])
    assert not (tmp_path / "unused").exists()


def test_container_composes_storage_only_when_requested(tmp_path: Path) -> None:
    root = tmp_path / "lazy"
    with Container(workspace=str(root)) as container:
        container.execute("help")
        container.execute("schema", ["job wait"])
        container.execute("generate")
        assert not root.exists()
        repositories = container.repositories
        assert container.resolved_workspace.root == root
        assert repositories is container.repositories
        assert container.artifact_store.repositories is repositories
        assert container.artifact_store.root == root / "artifacts"
        assert container.private_state_store.root == root / "private"
        assert repositories.list("sessions") == []
        connection = repositories.connection
    import sqlite3

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
    # Reusing a container must not return repositories backed by a closed connection.
    assert container.repositories.list("sessions") == []
    container.close()
    container.close()


def test_unused_container_close_does_not_create_storage(tmp_path: Path) -> None:
    root = tmp_path / "unused"
    container = Container(workspace=str(root))
    container.close()
    assert not root.exists()
