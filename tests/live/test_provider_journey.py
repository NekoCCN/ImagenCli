"""Per-provider journey; each provider skips unless its own credentials exist."""

import os
from pathlib import Path

import pytest

from tests.live.journey import JOURNEYS, replay, run

pytestmark = pytest.mark.live


def _root() -> Path:
    configured = os.environ.get("IMAGEN_LIVE_OUTPUT")
    if not configured:
        pytest.skip("IMAGEN_LIVE_OUTPUT is required")
    root = Path(configured).resolve() / "journeys"
    return root


@pytest.mark.parametrize("journey", JOURNEYS, ids=[item.name for item in JOURNEYS])
def test_provider_journey(journey):
    if not os.environ.get(journey.credential_env):
        pytest.skip(f"{journey.credential_env} is not set")
    if not os.environ.get(journey.base_url_env):
        pytest.skip(f"{journey.base_url_env} is not set")
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    dossier = run(journey, root)
    assert dossier["failures"] == [], dossier["failures"]
    assert all(item["passed"] for item in dossier["assertions"])
    results = replay(dossier, directory=root / journey.name / dossier["run_id"])
    assert all(item["passed"] for item in results), results
