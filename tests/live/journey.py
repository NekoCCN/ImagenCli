"""Provider journey harness with a redacted dossier and offline replay.

Live runs are explicit and bounded: at most two creates per journey, each run in
its own directory under live-results/. Nothing here runs without credentials.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = "imagen.live-dossier/v1"
MAX_CREATES = 2
SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization|api[-_]?key|x-goog-api-key)\s*[:=]\s*\S+"),
    re.compile(r"://[^/\s:@]+:[^/\s@]+@"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"),
)
PROMPT = (
    "Create an anime cel-shaded illustration of a girl with long white hair and red "
    "eyes, wearing a fully covering everyday outfit, standing in a sunny garden. "
    "Wholesome, non-sexual, crisp linework, no lettering."
)


@dataclass(frozen=True)
class Journey:
    name: str
    provider: str
    family: str
    model: str
    base_url_env: str
    credential_env: str
    operations: tuple[str, ...] = ("generate",)
    model_env: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def resolved_model(self) -> str:
        if self.model_env:
            return os.environ.get(self.model_env, self.model)
        return self.model


JOURNEYS = (
    Journey(
        name="openai-images", provider="openai", family="images",
        model="gpt-image-1.5", base_url_env="IMAGEN_LIVE_OPENAI_BASE_URL",
        credential_env="IMAGEN_LIVE_OPENAI_KEY", operations=("generate", "edit"),
        model_env="IMAGEN_LIVE_OPENAI_MODEL",
    ),
    Journey(
        name="gemini", provider="gemini", family="generate_content",
        model="gemini-3-pro-image-preview", base_url_env="IMAGEN_LIVE_GEMINI_BASE_URL",
        credential_env="IMAGEN_LIVE_GEMINI_KEY", operations=("generate",),
        model_env="IMAGEN_LIVE_GEMINI_MODEL",
    ),
    Journey(
        name="openai-compatible", provider="openai_compatible", family="images",
        model="image-1", base_url_env="IMAGEN_LIVE_COMPAT_BASE_URL",
        credential_env="IMAGEN_LIVE_COMPAT_KEY", operations=("generate",),
        model_env="IMAGEN_LIVE_COMPAT_MODEL",
    ),
)


def redact(message: str, limit: int = 2000) -> str:
    text = message
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    text = "".join(char for char in text if char.isprintable() or char in "\n\t")
    return text[:limit]


def seed_capabilities(workspace: Path, journey: Journey, model: str) -> None:
    """Register intent-only records so planning can run before any live call."""
    from imagen_cli.application.container import Container
    from imagen_cli.domain.models import CapabilityRecord

    with Container(workspace=str(workspace)) as container:
        for operation in journey.operations:
            value = {
                "id": f"journey-{journey.name}-{operation}",
                "provider": journey.provider, "family": journey.family,
                "model": model, "model_revision": "journey-live",
                "operation": operation, "support_status": "I",
                "parameter_schema": {
                    "type": "object",
                    "properties": {
                        "count": {"const": 1},
                        "quality": {"const": "high"},
                        "size": {"const": "1024x1024"},
                    },
                    "additionalProperties": False,
                },
                "semantic_mapping": {"prompt": "prompt", "count": "n"},
                "input_limits": {"max_count": 1}, "transport_kind": "sync",
                "continuation_strategy": (
                    "explicit_images" if operation == "edit" else "none"
                ),
                "idempotency_contract": {}, "cancel_contract": {},
                "sources": [{"url": os.environ.get(journey.base_url_env, ""),
                             "date": "2026-10-24",
                             "locator": "live journey runtime endpoint"}],
                "checked_at": "2026-10-24T00:00:00Z",
                "offline_evidence": [], "live_evidence": [],
            }
            value["digest"] = hashlib.sha256(
                json.dumps(value, sort_keys=True).encode()
            ).hexdigest()
            container.repositories.insert(
                "capabilities", CapabilityRecord.model_validate(value).model_dump(mode="json"),
            )


def _stamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class Cli:
    def __init__(self, workspace: Path, env: dict[str, str]) -> None:
        self.workspace = workspace
        self.env = env
        self.calls: list[dict[str, Any]] = []

    def __call__(self, *arguments: str, expected: int = 0) -> dict[str, Any]:
        command = [
            sys.executable, "-m", "imagen_cli", *arguments,
            "--workspace", str(self.workspace), "--output", "json", "--no-interactive",
        ]
        started = time.monotonic()
        completed = subprocess.run(
            command, env=self.env, input="", capture_output=True, text=True,
            timeout=600, check=False,
        )
        duration = round((time.monotonic() - started) * 1000)
        outcome = "exit=" + str(completed.returncode)
        try:
            value = json.loads(completed.stdout)
            outcome = str(value.get("status", outcome))
        except json.JSONDecodeError:
            value = {"status": "invalid_response", "data": {}}
        operation = arguments[0] if arguments else "unknown"
        if len(arguments) > 1 and not arguments[1].startswith("-"):
            operation += " " + arguments[1]
        self.calls.append(
            {"operation": operation, "duration_ms": duration, "outcome": outcome}
        )
        if completed.returncode != expected:
            raise JourneyFailure(
                operation, value.get("error", {}).get("code", "unexpected_exit"),
                redact(completed.stderr or completed.stdout),
            )
        return value.get("data", {})


class JourneyFailure(Exception):
    def __init__(self, step: str, code: str, message: str) -> None:
        super().__init__(message)
        self.step, self.code, self.message = step, code, message

    def as_json(self) -> dict[str, str]:
        return {"step": self.step, "code": self.code, "message": self.message}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cost_is_honest(job: dict[str, Any]) -> bool:
    cost = job.get("cost")
    if not isinstance(cost, dict):
        return False
    if cost.get("estimate") is None and cost.get("actual") is None:
        return bool(cost.get("unknown_reason"))
    return True


def run(journey: Journey, root: Path, *, prompt: str = PROMPT) -> dict[str, Any]:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)
    directory = root / journey.name / run_id
    directory.mkdir(parents=True, exist_ok=False)
    workspace = directory / "workspace"
    workspace.mkdir()
    prompt_file = directory / "prompt.txt"
    prompt_file.write_text(prompt, encoding="utf-8")
    base_url = os.environ.get(journey.base_url_env, "")
    env = os.environ.copy()
    env.pop("IMAGEN_CACHE_STAGING_DAYS", None)
    assertions: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    artifacts: list[dict[str, Any]] = []
    started_at = _stamp()
    dossier: dict[str, Any] = {
        "schema": SCHEMA, "run_id": run_id, "provider": journey.provider,
        "family": journey.family, "model": journey.resolved_model(),
        "started_at": started_at, "finished_at": "", "parameters": {},
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "cost": {"estimate": None, "actual": None,
                 "unknown_reason": "provider did not supply a verifiable billing amount"},
        "calls": [], "artifacts": [], "assertions": [], "failures": [],
    }
    cli = Cli(workspace, env)

    def check(name: str, passed: bool) -> None:
        assertions.append({"name": name, "passed": bool(passed)})

    try:
        seed_capabilities(workspace, journey, journey.resolved_model())
        profile = "live-" + run_id
        cli(
            "profile", "set", profile, "--provider", journey.provider,
            "--family", journey.family, "--base-url", base_url,
            "--model-default", journey.resolved_model(), "--credential-ref",
            f"env:{journey.credential_env}", "--revision", "1",
        )
        session = "live-" + run_id
        cli("session", "create", session, "--profile", profile,
            "--model", journey.resolved_model())
        pending = cli(
            "generate", "--session", session, "--prompt-file", str(prompt_file),
            "--size", "1024x1024", expected=6,
        )["plan"]
        dossier["parameters"] = pending.get("normalized_parameters", {})
        cli("plan", "approve", pending["id"], "--digest", pending["plan_digest"])
        executed = cli("plan", "execute", pending["id"])
        job = executed["job"]
        check("one create only", job.get("accepted_maybe") is not True)
        again = cli("plan", "execute", pending["id"])
        check("repeat execute reuses the job", again["job"]["id"] == job["id"])
        facts = cli("job", "get", job["id"])["job"]
        check("job succeeded", facts["state"] == "succeeded")
        check("remote succeeded", facts["remote_state"] == "succeeded")
        check("local save succeeded", facts["local_save_state"] == "saved")
        check("cost is explicit", _cost_is_honest(facts))
        for artifact in executed["artifacts"]:
            path = Path(artifact["path"])
            artifacts.append({
                "sha256": artifact["sha256"], "width": artifact["width"],
                "height": artifact["height"], "byte_size": artifact["byte_size"],
            })
            check("artifact bytes match", path.exists() and _sha(path) == artifact["sha256"])
            (directory / f"{artifact['id']}.png").write_bytes(path.read_bytes())
        first = executed["artifacts"][0]
        if "edit" in journey.operations:
            edit_plan = cli(
                "edit", "--session", session, "--input", "artifact:" + first["id"],
                "--prompt-file", str(prompt_file), "--size", "1024x1024", expected=6,
            )["plan"]
            cli("plan", "approve", edit_plan["id"], "--digest", edit_plan["plan_digest"])
            edited = cli("plan", "execute", edit_plan["id"])
            dossier["parameters"]["edit_requested"] = True
            check("edit produced a second artifact", len(edited["artifacts"]) == 1)
            for artifact in edited["artifacts"]:
                path = Path(artifact["path"])
                artifacts.append({
                    "sha256": artifact["sha256"], "width": artifact["width"],
                    "height": artifact["height"], "byte_size": artifact["byte_size"],
                })
                check(
                    "edited bytes match",
                    path.exists() and _sha(path) == artifact["sha256"],
                )
                (directory / f"{artifact['id']}.png").write_bytes(path.read_bytes())
            check(
                "edit reuses the same session",
                edited["job"]["turn_id"] != job["turn_id"],
            )
            facts = cli("session", "show", session)
            check("session version advanced", facts["session"]["version"] >= 1)
        exported = directory / "session.zip"
        export = cli("session", "export", session, "--to", str(exported))
        imported = cli("session", "import", str(exported))
        check(
            "export round trip keeps artifacts",
            imported.get("artifacts") == export.get("artifacts") and imported["artifacts"] > 0,
        )
        check("export has no omissions", export.get("omissions") == [])
        deleted = cli("session", "delete", session, "--yes")
        check("session deleted", deleted.get("session", {}).get("deleted_at") is not None)
        audit_env = dict(env)
        audit_env["IMAGEN_CACHE_DELETED_SESSION_DAYS"] = "0"
        auditor = Cli(workspace, audit_env)
        entries = auditor("cache", "audit")["entries"]
        check(
            "deleted session artifact is reclaimable",
            any(entry["kind"] == "deleted_session_artifact" for entry in entries),
        )
    except JourneyFailure as failure:
        failures.append(failure.as_json())
    except Exception as error:  # noqa: BLE001 - every failure belongs in the dossier
        failures.append({"step": "harness", "code": type(error).__name__,
                         "message": redact(str(error))})
    dossier["assertions"] = assertions
    dossier["failures"] = failures
    dossier["artifacts"] = artifacts
    dossier["calls"] = cli.calls
    dossier["finished_at"] = _stamp()
    (directory / "dossier.json").write_text(
        json.dumps(dossier, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return dossier


def replay(dossier: dict[str, Any], *, directory: Path | None = None) -> list[dict[str, Any]]:
    """Recheck the assertions a dossier can prove without a provider."""
    results: list[dict[str, Any]] = []

    def check(name: str, passed: bool) -> None:
        results.append({"name": name, "passed": bool(passed)})

    check("schema is v1", dossier.get("schema") == SCHEMA)
    check("no prompt text is stored", "prompt" not in dossier)
    creates = [
        call for call in dossier.get("calls", [])
        if call.get("operation") in {"generate", "edit"}
    ]
    check("calls within the create guard", len(creates) <= MAX_CREATES)
    recorded = dossier.get("assertions", [])
    check("recorded assertions passed", all(item.get("passed") for item in recorded))
    check("no recorded failures", not dossier.get("failures"))
    cost = dossier.get("cost")
    check(
        "cost is never silently zero",
        isinstance(cost, dict)
        and (
            (cost.get("estimate") is not None and cost.get("actual") is not None)
            or bool(cost.get("unknown_reason"))
        ),
    )
    if directory is not None:
        for artifact in dossier.get("artifacts", []):
            matches = list(directory.glob("*.png"))
            check(
                f"artifact {artifact['sha256'][:12]} is present",
                any(_sha(path) == artifact["sha256"] for path in matches),
            )
    return results
