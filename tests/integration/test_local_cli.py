"""Exercise local commands through independent, network-disabled CLI processes."""

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from imagen_cli.application.container import Container
from imagen_cli.application.session_service import SessionService

ROOT = Path(__file__).resolve().parents[2]


def invoke(workspace: Path, *args: str) -> tuple[int, dict[str, Any]]:
    env = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(ROOT / "src"),
        "HOME": str(workspace.parent / "unused-home"),
        "NO_COLOR": "1",
    }
    script = (
        "import socket,sys; "
        "socket.socket=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network')); "
        "socket.getaddrinfo=socket.socket; "
        "from imagen_cli.cli.entrypoint import main; sys.exit(main())"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, "--workspace", str(workspace), *args],
        input=b"",
        env=env,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert proc.stderr == b""
    records = [json.loads(line) for line in proc.stdout.splitlines()]
    value = records[-1].get("result", records[-1])
    return proc.returncode, value


def test_sessions_persist_across_cli_processes(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    intent = tmp_path / "intent.txt"
    intent.write_text("原始意图\n", encoding="utf-8")
    code, created = invoke(
        workspace, "session", "create", "海报", "--intent-file", str(intent),
        "--profile=local", "--model", "explicit",
    )
    assert code == 0
    row = created["data"]["session"]
    assert (row["name"], row["intent"], row["default_profile_id"], row["default_model"]) == (
        "海报", "原始意图\n", "local", "explicit",
    )
    assert invoke(workspace, "session", "create", "海报")[0] == 5
    assert invoke(workspace, "session", "create", "second")[0] == 0
    code, page = invoke(workspace, "session", "list", "--limit=1")
    assert code == 0
    assert [item["id"] for item in page["data"]["sessions"]] == [row["id"]]
    code, following = invoke(
        workspace, "session", "list", "--limit", "1", "--cursor", page["data"]["next_cursor"]
    )
    assert code == 0
    assert [item["name"] for item in following["data"]["sessions"]] == ["second"]
    assert following["data"]["next_cursor"] is None
    for identity in (row["id"], "海报"):
        for command in ("show", "resume"):
            code, value = invoke(workspace, "session", command, identity, "--output=jsonl")
            assert code == 0
            assert value["data"]["session"] == row
            assert value["data"]["turns"] == []
    assert invoke(workspace, "session", "show", "missing")[0] == 4
    with Container(workspace=str(workspace)) as container:
        assert container.repositories.list("jobs") == []


def seed_job(workspace: Path, *, state: str = "unknown", staging: bool = False) -> str:
    with Container(workspace=str(workspace)) as container:
        service = SessionService(container.repositories, container.artifact_store)
        session = service.create_session("seed")
        turn = service.create_turn(
            session.id, instruction="offline", operation="generate",
            resolved_profile_snapshot={}, model="local", parameters={},
        )
        container.repositories.update("jobs", turn.job_id, {"state": state})
        image = io.BytesIO()
        Image.new("RGB", (2, 2)).save(image, format="PNG")
        if staging:
            container.artifact_store.stage(
                "art_" + "1" * 32, image.getvalue(), producer_job_id=turn.job_id,
                producer_turn_id=turn.id,
                origin_manifest={"kind": "provider_output", "output_index": 0},
            )
        return turn.job_id


def test_job_queries_and_recovery_across_cli_processes(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    identity = seed_job(workspace, staging=True)
    code, value = invoke(workspace, "job", "get", identity)
    assert code == 0
    assert value["data"]["job"]["state"] == "unknown"
    code, value = invoke(workspace, "job", "wait", identity, "--timeout=1")
    assert code == 6
    assert value["status"] == "unknown"
    assert invoke(workspace, "job", "fetch", identity)[1]["status"] == "save_failed"
    code, value = invoke(workspace, "job", "reconcile", identity)
    assert code == 0
    saved = Path(value["data"]["artifacts"][0]["path"])
    assert saved.is_absolute() and saved.is_file()
    assert value["data"]["job"]["state"] == "unknown"
    assert invoke(workspace, "job", "fetch", identity)[0] == 0
    saved.unlink()
    assert invoke(workspace, "job", "fetch", identity)[1]["status"] == "save_failed"
    with Container(workspace=str(workspace)) as container:
        assert len(container.repositories.list("jobs")) == 1
        assert container.repositories.get("jobs", identity)["state"] == "unknown"


def test_job_wait_uses_requested_timeout(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    identity = seed_job(workspace, state="queued")
    code, value = invoke(workspace, "job", "wait", identity, "--timeout", "1")
    assert code == 6
    assert value["status"] == "wait_timeout"
    assert value["data"]["job"]["state"] == "queued"


@pytest.mark.parametrize("operation", ["get", "wait", "fetch", "reconcile"])
def test_missing_job_is_not_found(tmp_path: Path, operation: str) -> None:
    code, value = invoke(tmp_path / "work", "job", operation, "job_" + "2" * 32)
    assert code == 4
    assert value["status"] == "not_found"


@pytest.mark.parametrize(
    "args",
    [
        ("session", "create"),
        ("session", "create", "name", "extra"),
        ("session", "create", "name", "--model", "one", "--model=two"),
        ("session", "create", "name", "--model", "--profile", "local"),
        ("session", "show"),
        ("session", "resume", "one", "two"),
        ("session", "list", "extra"),
        ("session", "list", "--unknown=value"),
        ("session", "list", "--limit=1001"),
        ("session", "list", "--cursor=bad!"),
        ("session", "show", "name", "--limit=1"),
        ("session", "create", "name", "--dry-run"),
        ("job", "get", "wrong"),
        ("job", "get", "job_" + "1" * 32, "--timeout=1"),
        ("job", "wait", "job_" + "1" * 32, "--timeout=0"),
        ("schema", "session", "create", "extra"),
        ("help", "session", "create", "extra"),
        ("session", "list", "-x"),
        ("session", "list", "--agent"),
    ],
)
def test_invalid_inputs_are_strict_and_do_not_create_workspace(tmp_path: Path, args: tuple) -> None:
    workspace = tmp_path / "unused"
    code, value = invoke(workspace, *args)
    assert code == 2
    assert value["status"] == "validation_error"
    assert not workspace.exists()


@pytest.mark.parametrize(
    "args", [("help",), ("schema", "session", "create"),
             ("session", "create", "--help"), ("job", "wait", "--help")],
)
def test_discovery_never_initializes_storage(tmp_path: Path, args: tuple) -> None:
    workspace = tmp_path / "unused"
    assert invoke(workspace, *args)[0] == 0
    assert not workspace.exists()


@pytest.mark.parametrize("command", ["generate", "edit"])
def test_network_creation_routes_remain_unsupported(tmp_path: Path, command: str) -> None:
    workspace = tmp_path / "unused"
    code, value = invoke(workspace, command)
    assert code == 2
    assert value["status"] == "unsupported_capability"
    assert not workspace.exists()


def test_ops_and_superres_catalog_are_offline(tmp_path: Path):
    workspace = tmp_path / "ws"
    code, result = invoke(workspace, "ops", "--output", "json", "--no-interactive")
    assert code == 0 and result["status"] == "succeeded"
    assert "superres" in result["data"]["operations"]
    code, result = invoke(
        workspace, "superres", "models", "--output", "json", "--no-interactive"
    )
    assert code == 0 and result["status"] == "succeeded"
    names = {item["model"] for item in result["data"]["models"]}
    assert {"realesrgan-x4plus", "realesrgan-x4plus-anime", "swinir-s-x4"} <= names
    assert all("downloaded" in item for item in result["data"]["models"])
