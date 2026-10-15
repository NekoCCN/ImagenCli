"""Offline deterministic fixtures without provider or credential access."""

from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from PIL import Image


@pytest.fixture
def isolated_workspace(tmp_path: Path) -> Path:
    return tmp_path / "workspace"


@pytest.fixture
def fixed_time() -> datetime:
    return datetime(2026, 10, 23, tzinfo=UTC)


@pytest.fixture
def png_bytes() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (2, 3), "red").save(stream, format="PNG")
    return stream.getvalue()


@pytest.fixture
def malformed_download() -> bytes:
    return b"not an image\x00"


@pytest.fixture
def secret_sample() -> str:
    return "fixture-only-secret-never-a-real-credential"


@pytest.fixture
def mock_transport(png_bytes: bytes) -> httpx.MockTransport:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=png_bytes, headers={"content-type": "image/png"})

    return httpx.MockTransport(respond)
