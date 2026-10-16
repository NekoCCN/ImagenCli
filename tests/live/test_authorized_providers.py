"""Explicitly authorized local gateway journey; ordinary CI never sends requests."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from imagen_cli.application.container import Container
from imagen_cli.domain.models import CapabilityRecord

pytestmark = pytest.mark.skipif(
    os.environ.get("IMAGEN_LIVE_LOCAL") != "1",
    reason="requires explicit local gateway authorization (two creates maximum)",
)


def test_local_gateway_generate_resume_edit(tmp_path):
    endpoint = "http://127.0.0.1:3000/v1"
    model = os.environ.get("IMAGEN_LIVE_MODEL", "A6N/gpt-image-1.5")
    root = Path(os.environ["IMAGEN_LIVE_OUTPUT"]).resolve()
    root.mkdir(parents=True, exist_ok=False)
    workspace = root / "workspace"
    env = os.environ.copy()
    assert env.get("IMAGEN_LIVE_KEY"), "explicit credential environment required"
    calls = []

    def cli(*args, expected=0):
        command = [sys.executable, "-m", "imagen_cli", *args,
                   "--workspace", str(workspace), "--output", "json", "--no-interactive"]
        completed = subprocess.run(command, env=env, input="", capture_output=True,
                                   text=True, timeout=300, check=False)
        assert not completed.stderr
        value = json.loads(completed.stdout)
        calls.append({"arguments": list(args), "exit_code": completed.returncode,
                      "result": value})
        (root / "commands.json").write_text(json.dumps(calls, ensure_ascii=False, indent=2))
        assert completed.returncode == expected, value
        return value["data"]

    config = root / "profile.toml"
    config.write_text(
        f'id = "local-live"\nname = "local-live"\nprovider = "openai"\n'
        f'family = "images"\nbase_url = "{endpoint}"\nmodel_default = "{model}"\n'
        'credential_ref = "env:IMAGEN_LIVE_KEY"\nrevision = 1\n'
        f'[trust_policy]\nallowed_upload_targets = ["{endpoint}"]\n',
    )
    cli("profile", "add", "local-live", "--config-file", str(config))
    with Container(workspace=str(workspace)) as container:
        for operation in ("generate", "edit"):
            record = {
                "id": f"local-live-{operation}", "provider": "openai", "family": "images",
                "model": model, "model_revision": "gateway-observed-2026-10-23",
                "operation": operation, "support_status": "I",
                "parameter_schema": {"type": "object", "properties": {
                    "count": {"const": 1}, "quality": {"const": "high"},
                    "size": {"const": "1024x1024"}}, "additionalProperties": False},
                "semantic_mapping": {"prompt": "prompt", "count": "n"},
                "input_limits": {"max_count": 1}, "transport_kind": "sync",
                "continuation_strategy": "explicit_images" if operation == "edit" else "none",
                "idempotency_contract": {}, "cancel_contract": {},
                "sources": [{"url": "http://127.0.0.1:3000/v1/models",
                             "date": "2026-10-23", "locator": "explicit gateway model"}],
                "checked_at": "2026-10-23T00:00:00Z",
                "offline_evidence": [], "live_evidence": [],
            }
            record["digest"] = hashlib.sha256(
                json.dumps(record, sort_keys=True).encode()).hexdigest()
            container.repositories.insert("capabilities", CapabilityRecord.model_validate(
                record).model_dump(mode="json"))
    cli("session", "create", "live-demo", "--profile", "local-live", "--model", model)
    parameters = root / "parameters.json"
    parameters.write_text('{"quality":"high"}')
    evidence = []
    for operation in ("generate", "edit"):
        prompt = root / f"{operation}.txt"
        prompt.write_text(
            "Create an anime cel-shaded illustration of a young girl with long white hair "
            "and red eyes, wearing a fully covering everyday outfit, standing in a sunny "
            "flower garden. Wholesome, non-sexual, crisp linework, no lettering."
            if operation == "generate" else
            "Keep the same character, long white hair, red eyes, fully covering everyday "
            "outfit and anime cel-shaded style. Change the garden flowers to blue and add "
            "a small butterfly. Wholesome, non-sexual, no lettering.",
        )
        args = [operation, "--session", "live-demo", "--prompt-file", str(prompt),
                "--provider-params-file", str(parameters), "--size", "1024x1024"]
        if evidence:
            cli("session", "resume", "live-demo")
            args += ["--input", "artifact:" + evidence[0]["id"]]
        plan = cli(*args, expected=6)["plan"]
        cli("plan", "approve", plan["id"], "--digest", plan["plan_digest"])
        result = cli("plan", "execute", plan["id"])
        assert result["job"]["state"] == "succeeded"
        assert len(result["artifacts"]) == 1
        artifact = result["artifacts"][0]
        content = Path(artifact["path"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == artifact["sha256"]
        with Image.open(artifact["path"]) as image:
            image.verify()
        (root / f"{operation}.png").write_bytes(content)
        evidence.append(artifact)
        repeated = cli("plan", "execute", plan["id"])
        assert repeated["job"]["id"] == result["job"]["id"]
    history = cli("session", "show", "live-demo")
    assert len(history["turns"]) == 2
    assert history["session"]["version"] == 2
    assert history["turns"][1]["parent_turn_id"] == history["turns"][0]["id"]
    with Container(workspace=str(workspace)) as container:
        assert len(container.repositories.list("jobs")) == 2
    assert env["IMAGEN_LIVE_KEY"] not in (root / "commands.json").read_text()
