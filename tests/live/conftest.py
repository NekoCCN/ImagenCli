"""Live tests require explicit authorization, budget and an empty output root."""

import os

import pytest

REQUIRED = ("IMAGEN_LIVE_LOCAL", "IMAGEN_LIVE_KEY", "IMAGEN_LIVE_OUTPUT", "IMAGEN_LIVE_BUDGET")


@pytest.fixture(scope="session")
def live_authorization() -> int:
    if os.environ.get("IMAGEN_LIVE_LOCAL") != "1":
        pytest.skip("requires explicit local gateway authorization")
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        pytest.skip(f"missing live authorization: {', '.join(missing)}")
    budget = int(os.environ["IMAGEN_LIVE_BUDGET"])
    if not 1 <= budget <= 10:
        pytest.skip("live budget must be between 1 and 10 creates")
    return budget
