"""Dossier replay and redaction run without any provider access."""

import json
from pathlib import Path

from tests.live import journey


def sample(**overrides):
    value = {
        "schema": journey.SCHEMA,
        "run_id": "run-1",
        "provider": "openai",
        "family": "images",
        "model": "m",
        "started_at": "2026-10-24T00:00:00Z",
        "finished_at": "2026-10-24T00:01:00Z",
        "parameters": {},
        "prompt_sha256": "a" * 64,
        "cost": {"estimate": None, "actual": None, "unknown_reason": "not billed"},
        "calls": [
            {"operation": "generate", "duration_ms": 5, "outcome": "needs_approval"},
            {"operation": "plan execute", "duration_ms": 5, "outcome": "succeeded"},
        ],
        "artifacts": [],
        "assertions": [{"name": "job succeeded", "passed": True}],
        "failures": [],
    }
    value.update(overrides)
    return value


def test_replay_accepts_a_clean_dossier():
    results = journey.replay(sample())
    assert all(item["passed"] for item in results)


def test_replay_rejects_hidden_failures_and_zero_cost():
    dossier = sample(
        failures=[{"step": "execute", "code": "provider_error", "message": "boom"}],
        cost={"estimate": None, "actual": 0, "unknown_reason": ""},
    )
    results = journey.replay(dossier)
    failed = {item["name"] for item in results if not item["passed"]}
    assert "no recorded failures" in failed
    assert "cost is never silently zero" in failed


def test_replay_checks_recorded_artifacts(tmp_path: Path):
    image = tmp_path / "out.png"
    image.write_bytes(b"pretend png")
    digest = __import__("hashlib").sha256(b"pretend png").hexdigest()
    dossier = sample(artifacts=[{"sha256": digest, "width": 1, "height": 1,
                                 "byte_size": len(b"pretend png")}])
    results = journey.replay(dossier, directory=tmp_path)
    assert all(item["passed"] for item in results)


def test_redaction_strips_credentials_and_truncates():
    message = (
        "POST https://user:pass@example.test/v1 Authorization: Bearer abc.def\n"
        + "x" * 5000
    )
    cleaned = journey.redact(message)
    assert "user:pass" not in cleaned
    assert "Bearer abc" not in cleaned
    assert len(cleaned) <= 2000


def test_dossier_schema_has_no_prompt_text():
    dossier = sample()
    encoded = json.dumps(dossier)
    assert "prompt" not in dossier
    assert "garden" not in encoded
