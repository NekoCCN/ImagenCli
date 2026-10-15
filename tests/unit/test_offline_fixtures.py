from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image


def test_shared_offline_fixtures(
    isolated_workspace: Path,
    fixed_time: datetime,
    png_bytes: bytes,
    malformed_download: bytes,
    secret_sample: str,
    mock_transport: httpx.MockTransport,
) -> None:
    assert not isolated_workspace.exists()
    assert fixed_time.tzinfo == UTC
    with Image.open(BytesIO(png_bytes)) as image:
        assert image.size == (2, 3)
        assert image.format == "PNG"
    assert malformed_download != png_bytes
    assert secret_sample.startswith("fixture-only-")
    with httpx.Client(transport=mock_transport) as client:
        response = client.get("https://fixture.invalid/image")
    assert response.content == png_bytes
