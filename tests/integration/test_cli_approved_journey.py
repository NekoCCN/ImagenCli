"""Full approved CLI journey across processes, mocking only provider HTTP."""

import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from imagen_cli.application.container import Container
from tests.unit.test_capabilities import capability


def test_cli_generate_resume_edit_dispatches_once_per_plan(tmp_path, png_bytes):
    workspace = tmp_path / "workspace"
    dispatches = tmp_path / "dispatches.jsonl"
    env = os.environ.copy()
    env["IMAGEN_TEST_KEY"] = "offline-fixture-secret"
    script = f"""
import base64, json, socket, sys
from pathlib import Path
import httpx

def respond(request):
    with Path({str(dispatches)!r}).open('a') as log:
        log.write(json.dumps({{'path': request.url.path,
                              'body': request.content.decode('latin-1')}}) + '\\n')
    return httpx.Response(200, json={{'data': [
        {{'b64_json': {base64.b64encode(png_bytes).decode()!r}}}
    ]}})
httpx.HTTPTransport = lambda **kwargs: httpx.MockTransport(respond)
socket.socket = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network forbidden'))
from imagen_cli.cli.entrypoint import main
sys.exit(main())
"""

    def cli(*args, expected=0):
        completed = subprocess.run(
            [sys.executable, "-c", script, *args, "--workspace", str(workspace),
             "--output", "json", "--no-interactive"],
            env=env, input="", text=True, capture_output=True, timeout=20, check=False,
        )
        assert completed.stderr == ""
        result = json.loads(completed.stdout)
        assert completed.returncode == expected, result
        assert env["IMAGEN_TEST_KEY"] not in completed.stdout
        return result["data"]

    profile = tmp_path / "profile.toml"
    profile.write_text(
        'id = "offline"\nname = "offline"\nprovider = "openai"\n'
        'family = "images"\nbase_url = "https://offline.example.test/v1"\n'
        'model_default = "image-model"\ncredential_ref = "env:IMAGEN_TEST_KEY"\n'
        'revision = 1\n[trust_policy]\n'
        'allowed_upload_targets = ["https://offline.example.test/v1"]\n',
    )
    cli("profile", "add", "offline", "--config-file", str(profile))
    with Container(workspace=str(workspace)) as container:
        for operation in ("generate", "edit"):
            container.repositories.insert("capabilities", capability(
                id=f"offline-{operation}", operation=operation,
                parameter_schema={"type": "object", "properties": {"count": {"const": 1}},
                                  "additionalProperties": False},
                continuation_strategy="explicit_images" if operation == "edit" else "none",
            ))
    cli("session", "create", "journey", "--profile", "offline", "--model", "image-model")
    outputs = []
    for operation in ("generate", "edit"):
        prompt = tmp_path / f"{operation}.txt"
        prompt.write_text("二次元赛璐璐，白色长发、红色瞳孔，完整日常服装的非性化角色。")
        args = [operation, "--session", "journey", "--prompt-file", str(prompt)]
        if outputs:
            restored = cli("session", "resume", "journey")
            assert len(restored["turns"]) == 1
            args += ["--input", "artifact:" + outputs[0]["id"]]
        plan = cli(*args, expected=6)["plan"]
        assert len(dispatches.read_text().splitlines()) == len(outputs) if outputs else (
            not dispatches.exists()
        )
        cli("plan", "approve", plan["id"], "--digest", plan["plan_digest"])
        result = cli("plan", "execute", plan["id"])
        assert result["job"]["state"] == "succeeded"
        artifact = result["artifacts"][0]
        assert Path(artifact["path"]).read_bytes() == png_bytes
        assert artifact["sha256"] == hashlib.sha256(png_bytes).hexdigest()
        assert cli("plan", "execute", plan["id"]) == result
        outputs.append(artifact)
    sent = [json.loads(line) for line in dispatches.read_text().splitlines()]
    assert [request["path"] for request in sent] == ["/v1/images/generations", "/v1/images/edits"]
    assert json.loads(sent[0]["body"])["model"] == "image-model"
    assert 'name="image[]"' in sent[1]["body"]
    history = cli("session", "show", "journey")
    assert history["session"]["version"] == 2
    assert len(history["turns"]) == 2
    assert history["turns"][1]["parent_turn_id"] == history["turns"][0]["id"]
    assert outputs[0]["id"] != outputs[1]["id"]
