"""Shared bounded HTTP, authorization checks and image decoding for route adapters."""

import base64
import binascii
import io
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from PIL import Image, UnidentifiedImageError

from .base import ProviderFailure, ValidatedRequest

MAX_RESPONSE_BYTES = 384 * 1024 * 1024
MAX_IMAGE_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 100_000_000
MAX_INPUT_BYTES = 256 * 1024 * 1024


def require_authorized(request: ValidatedRequest) -> None:
    if request.authorized is not True:
        raise ProviderFailure("approval_required")
    if request.dispatch_intent_recorded is not True:
        raise ProviderFailure("validation_error")


def require_profile(request: ValidatedRequest, profile: Any) -> None:
    if (
        request.profile_identity != profile.id
        or request.profile_revision != profile.revision
    ):
        raise ProviderFailure("validation_error")


def check_parameters(parameters: Mapping[str, Any], allowed: Iterable[str]) -> None:
    if set(parameters) - set(allowed):
        raise ProviderFailure("validation_error")


def check_inputs(
    inputs: tuple[bytes, ...], mask: bytes | None, *, max_image_bytes: int = MAX_IMAGE_BYTES,
) -> None:
    images = (*inputs, *((mask,) if mask is not None else ()))
    if (
        any(not image or len(image) > max_image_bytes for image in images)
        or sum(map(len, images)) > MAX_INPUT_BYTES
    ):
        raise ProviderFailure("validation_error")


def client(
    transport: httpx.BaseTransport | None, *, read_timeout: float = 60.0,
) -> httpx.Client:
    return httpx.Client(
        transport=transport or httpx.HTTPTransport(retries=0, trust_env=False),
        follow_redirects=False,
        trust_env=False,
        timeout=httpx.Timeout(60.0, connect=10.0, read=read_timeout),
    )


@dataclass(frozen=True)
class BoundedResponse:
    status: int
    headers: Mapping[str, str]
    content: bytes


def send(
    http: httpx.Client,
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    json: Any = None,
    data: Any = None,
    files: Any = None,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> BoundedResponse:
    request = http.build_request(
        method, url, headers=headers, json=json, data=data, files=files,
    )
    response = http.send(request, stream=True)
    try:
        raw = bytearray()
        for chunk in response.iter_bytes():
            if len(raw) + len(chunk) > max_bytes:
                raise ProviderFailure("provider_error", accepted_maybe=True)
            raw.extend(chunk)
        return BoundedResponse(response.status_code, dict(response.headers), bytes(raw))
    finally:
        response.close()


def failure_for_status(status: int, *, accepted_maybe: bool | None = None) -> ProviderFailure:
    if status in {400, 422}:
        kind = "validation_error"
    elif status == 401:
        kind = "authentication_error"
    elif status == 403:
        kind = "permission_denied"
    elif status == 404:
        kind = "not_found"
    elif 300 <= status < 400:
        kind = "outcome_unknown"
    else:
        kind = "provider_error"
    maybe = status >= 500 or 300 <= status < 400
    return ProviderFailure(kind, accepted_maybe=maybe if accepted_maybe is None else accepted_maybe)


def decode_image(
    encoded: str, *, max_bytes: int = MAX_IMAGE_BYTES, max_pixels: int = MAX_PIXELS,
) -> tuple[bytes, str]:
    if not isinstance(encoded, str) or not encoded:
        raise ValueError("missing image")
    if len(encoded) > 4 * ((max_bytes + 2) // 3):
        raise ValueError("image limit")
    content = base64.b64decode(encoded, validate=True)
    return validate_image_bytes(content, max_bytes=max_bytes, max_pixels=max_pixels)


def validate_image_bytes(
    content: bytes, *, max_bytes: int = MAX_IMAGE_BYTES, max_pixels: int = MAX_PIXELS,
) -> tuple[bytes, str]:
    if not content or len(content) > max_bytes:
        raise ValueError("image limit")
    with Image.open(io.BytesIO(content)) as image:
        image.verify()
        if image.width * image.height > max_pixels:
            raise ValueError("pixel limit")
        media_type = Image.MIME.get(image.format or "", "image/png")
    return content, media_type


def data_url(content: bytes) -> str:
    media_type = validate_image_bytes(content)[1]
    return f"data:{media_type};base64,{base64.b64encode(content).decode('ascii')}"


def fetch_public_image(
    url: str,
    transport: httpx.BaseTransport | None,
    *,
    allowed_hosts: tuple[str, ...],
    max_bytes: int = MAX_IMAGE_BYTES,
) -> tuple[bytes, str]:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or not any(hostname == host or hostname.endswith("." + host) for host in allowed_hosts)
    ):
        raise ProviderFailure("provider_error")
    try:
        with client(transport) as http:
            response = send(http, "GET", url, headers={"Accept-Encoding": "identity"},
                            max_bytes=max_bytes)
    except ProviderFailure:
        raise
    except (httpx.HTTPError, OSError):
        raise ProviderFailure("provider_error") from None
    if not 200 <= response.status < 300:
        raise failure_for_status(response.status, accepted_maybe=False)
    try:
        return validate_image_bytes(response.content, max_bytes=max_bytes)
    except (ValueError, UnidentifiedImageError, OSError, binascii.Error):
        raise ProviderFailure("provider_error") from None
