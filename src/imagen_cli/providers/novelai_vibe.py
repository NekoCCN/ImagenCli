"""Single-attempt, dispatch-only JSON or opt-in gateway multipart Vibe encoder."""

import base64
import hashlib
import ipaddress
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from imagen_cli.domain.models import ProviderProfile

from .base import ProviderFailure
from .common import client, failure_for_status, send, validate_image_bytes

MAX_ENCODING_BYTES = 16 * 1024 * 1024


def identity(
    profile: ProviderProfile, source_sha256: str, model: str, information_extracted: float
) -> dict[str, Any]:
    if (profile.provider, profile.family) != ("novelai", "generate_image"):
        raise ValueError("Vibe preparation requires a NovelAI generate_image profile")
    endpoint = urlsplit(profile.base_url)
    if endpoint.scheme == "http":
        host = endpoint.hostname or ""
        if host != "localhost" and not ipaddress.ip_address(host).is_loopback:
            raise ValueError("Vibe preparation requires HTTPS or loopback HTTP")
    if (
        type(information_extracted) not in {int, float}
        or not math.isfinite(information_extracted)
        or not 0 <= information_extracted <= 1
    ):
        raise ValueError("information_extracted must be finite and between zero and one")
    if not isinstance(model, str) or not model.strip() or len(model) > 256:
        raise ValueError("explicit model required")
    if len(source_sha256) != 64 or any(c not in "0123456789abcdef" for c in source_sha256):
        raise ValueError("source SHA-256 required")
    options = profile.novelai_options
    if options and options.reference_transport != "encoded":
        raise ValueError("Vibe preparation requires explicit encoded reference mode")
    result = {
        "profile_identity": profile.id,
        "profile_revision": profile.revision,
        "credential_ref_sha256": hashlib.sha256(profile.credential_ref.encode()).hexdigest(),
        "endpoint_root": profile.base_url,
        "endpoint": profile.base_url.rstrip("/") + "/ai/encode-vibe",
        "auth_mode": options.auth_mode if options else "bearer",
        "source_sha256": source_sha256,
        "model": model,
        "information_extracted": float(information_extracted),
        "format": "novelai-encode-vibe-binary-v1",
    }

    # Omit the default to preserve legacy JSON preparation identities and caches.
    if options and options.vibe_encoding_transport != "json":
        result["vibe_encoding_transport"] = options.vibe_encoding_transport
    return result


@dataclass(frozen=True)
class VibeEncodingRequest:
    identity: Mapping[str, Any]
    content: bytes = field(repr=False)
    authorized: bool = False
    dispatch_intent_recorded: bool = False


class NovelAIVibeAdapter:
    def __init__(
        self,
        profile: ProviderProfile,
        *,
        resolve_credential: Callable[[str], str],
        transport: httpx.BaseTransport | None = None,
        read_timeout: float = 300,
    ) -> None:
        self.profile = profile.model_copy(deep=True)
        self.resolve_credential = resolve_credential
        self.transport = transport
        self.read_timeout = read_timeout

    def encode(self, request: VibeEncodingRequest) -> bytes:
        if not request.authorized or not request.dispatch_intent_recorded:
            raise ProviderFailure("approval_required")
        try:
            _, media_type = validate_image_bytes(request.content)
            expected = identity(
                self.profile,
                hashlib.sha256(request.content).hexdigest(),
                request.identity["model"],
                request.identity["information_extracted"],
            )
            if dict(request.identity) != expected:
                raise ValueError("preparation identity drifted")
        except Exception:
            raise ProviderFailure("validation_error") from None
        try:
            token = self.resolve_credential(self.profile.credential_ref)
            if not token or any(c in token for c in "\r\n\0"):
                raise ValueError
        except Exception:
            raise ProviderFailure("authentication_error") from None
        headers = (
            {"Authorization": f"Bearer {token}"}
            if expected["auth_mode"] == "bearer"
            else {"X-API-Key": token}
        )
        headers.update({"Accept": "application/binary", "Accept-Encoding": "identity"})
        payload: dict[str, Any]
        if expected.get("vibe_encoding_transport", "json") == "multipart":
            payload = {
                "data": {
                    "model": expected["model"],
                    "information_extracted": str(expected["information_extracted"]),
                },
                "files": {"image": ("image", request.content, media_type)},
            }
        else:
            payload = {"json": {
                "image": base64.b64encode(request.content).decode("ascii"),
                "model": expected["model"],
                "information_extracted": expected["information_extracted"],
            }}
        try:
            with client(self.transport, read_timeout=self.read_timeout) as http:
                response = send(
                    http,
                    "POST",
                    expected["endpoint"],
                    headers=headers,
                    **payload,
                    max_bytes=MAX_ENCODING_BYTES,
                )
        except ProviderFailure:
            raise
        except (httpx.HTTPError, OSError, ValueError):
            raise ProviderFailure("outcome_unknown", accepted_maybe=True) from None
        if response.status == 429:
            raise ProviderFailure("rate_limited", http_status=429)
        if not 200 <= response.status < 300:
            raise failure_for_status(response.status)
        if (
            response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            not in {"application/binary", "application/octet-stream"}
            or not response.content
        ):
            raise ProviderFailure("provider_error", accepted_maybe=True)
        return response.content
