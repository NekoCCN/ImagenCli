import base64
import io
import json
import zipfile

import pytest

from imagen_cli.providers.base import ProviderFailure
from imagen_cli.providers.novelai_output import decode_output


def archive(rows):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, data in rows:
            z.writestr(name, data)
    return output.getvalue()


def test_zip(png_bytes, monkeypatch):
    monkeypatch.setattr(zipfile.ZipFile, "extract", lambda *a: pytest.fail("extraction"))
    monkeypatch.setattr(zipfile.ZipFile, "extractall", lambda *a: pytest.fail("extraction"))
    result = decode_output(
        archive([("image_0.png", png_bytes)]), "application/zip", requested_count=2
    )
    assert (result.requested_count, result.actual_count) == (2, 1)
    assert result.artifacts[0].content == png_bytes


def test_json_metadata(png_bytes):
    data = json.dumps(
        {"images": [{"image": base64.b64encode(png_bytes).decode(), "seed": 42, "index": 3}]}
    ).encode()
    result = decode_output(data, "application/json", requested_count=1)
    assert result.artifacts[0].index == 3
    assert result.private_response["seeds"] == (42,)


@pytest.mark.parametrize(
    "names",
    [[], ["../x.png"], ["/x.png"], ["a\\b.png"], ["a.png", "a.png"], ["sub/x.png"], ["x.txt"]],
)
def test_bad_archive_names(names, png_bytes):
    if len(names) != len(set(names)):
        with pytest.warns(UserWarning, match="Duplicate name"):
            content = archive([(name, png_bytes) for name in names])
    else:
        content = archive([(name, png_bytes) for name in names])
    with pytest.raises(ProviderFailure):
        decode_output(content, "application/zip", requested_count=1)


@pytest.mark.parametrize(
    "content,media",
    [
        (b"SECRET", "application/zip"),
        (b"{}", "application/json"),
        (b"SECRET", "text/html"),
        (archive([("x.png", b"not image")]), "application/zip"),
        (archive([("x.png", b"0" * 100000)]), "application/zip"),
    ],
)
def test_invalid(content, media):
    with pytest.raises(ProviderFailure) as error:
        decode_output(content, media, requested_count=1)
    assert "SECRET" not in str(error.value)


def test_limits(png_bytes, monkeypatch):
    import imagen_cli.providers.novelai_output as module

    for limit in ["MAX_RESPONSE_BYTES", "MAX_IMAGE_BYTES", "MAX_EXPANDED_BYTES", "MAX_PIXELS"]:
        with monkeypatch.context() as patch:
            patch.setattr(module, limit, 1)
            with pytest.raises(ProviderFailure):
                decode_output(archive([("x.png", png_bytes)]), "application/zip", requested_count=1)


def test_encrypted_flag(png_bytes):
    data = bytearray(archive([("x.png", png_bytes)]))
    for signature, offset in [(b"PK\x03\x04", 6), (b"PK\x01\x02", 8)]:
        pos = data.index(signature)
        data[pos + offset] |= 1
    with pytest.raises(ProviderFailure):
        decode_output(bytes(data), "application/zip", requested_count=1)


def test_member_count(png_bytes, monkeypatch):
    import imagen_cli.providers.novelai_output as module

    monkeypatch.setattr(module, "MAX_MEMBERS", 1)
    with pytest.raises(ProviderFailure):
        decode_output(
            archive([("a.png", png_bytes), ("b.png", png_bytes)]),
            "application/zip",
            requested_count=2,
        )


def test_json_partial_count(png_bytes):
    content = json.dumps({"images": [{"image": base64.b64encode(png_bytes).decode()}]}).encode()
    result = decode_output(content, "application/json; charset=utf-8", requested_count=2)
    assert result.actual_count == 1
    assert result.requested_count == 2
