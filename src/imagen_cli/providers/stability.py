"""Stability Stable Image: synchronous multipart generate/edit, declared sync only."""

import json
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx

from imagen_cli.domain.models import ProviderProfile

from .base import (
    CancelObservation,
    ImmediateResult,
    ProviderFailure,
    RemoteObservation,
    ValidatedRequest,
)
from .common import (
    check_inputs,
    check_parameters,
    client,
    decode_image,
    failure_for_status,
    require_authorized,
    require_profile,
    send,
    validate_image_bytes,
)

__all__ = ["StabilityAdapter"]

_ROUTES = {
    "core": ("generate", "/v2beta/stable-image/generate/core"),
    "ultra": ("generate", "/v2beta/stable-image/generate/ultra"),
    "sd3": ("generate", "/v2beta/stable-image/generate/sd3"),
    "inpaint": ("edit", "/v2beta/stable-image/edit/inpaint"),
    "erase": ("edit", "/v2beta/stable-image/edit/erase"),
    "outpaint": ("edit", "/v2beta/stable-image/edit/outpaint"),
}
_ALLOWED = {
    "core": {"count", "aspect_ratio", "seed", "output_format", "negative_prompt",
             "style_preset"},
    "ultra": {"count", "aspect_ratio", "seed", "output_format", "negative_prompt"},
    "sd3": {"count", "aspect_ratio", "seed", "output_format", "negative_prompt", "model"},
    "inpaint": {"count", "seed", "output_format", "negative_prompt"},
    "erase": {"count", "seed", "output_format"},
    "outpaint": {"count", "seed", "output_format", "left", "right", "up", "down"},
}
_MEDIA = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp"}


class StabilityAdapter:
    """No automatic retries; a stability 403 is a moderation refusal, not permission."""

    def __init__(
        self,
        profile: ProviderProfile,
        *,
        resolve_credential: Callable[[str], str],
        transport: httpx.BaseTransport | None = None,
        max_response_bytes: int = 384 * 1024 * 1024,
        read_timeout: float = 60.0,
    ) -> None:
        self.profile = profile
        self.resolve_credential = resolve_credential
        self.transport = transport
        self.max_response_bytes = min(max_response_bytes, 384 * 1024 * 1024)
        self.read_timeout = read_timeout

    def _base(self) -> str:
        parsed = urlsplit(self.profile.base_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "api.stability.ai"
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ProviderFailure("validation_error")
        return self.profile.base_url.rstrip("/")

    def validate(self, request: ValidatedRequest) -> None:
        require_authorized(request)
        require_profile(request, self.profile)
        if (
            self.profile.provider != "stability"
            or request.model not in _ROUTES
            or _ROUTES[request.model][0] != request.operation
            or request.native_context is not None
            or not request.model_revision
            or not request.capability_digest
        ):
            raise ProviderFailure("unsupported_capability")
        if request.model != "erase" and not request.prompt.strip():
            raise ProviderFailure("validation_error")
        check_parameters(request.normalized_parameters, _ALLOWED[request.model])
        check_inputs(request.inputs, request.mask)
        count = request.normalized_parameters.get("count", 1)
        if type(count) is not int or count != 1:
            raise ProviderFailure("validation_error")
        if request.normalized_parameters.get("output_format") not in {None, *_MEDIA}:
            raise ProviderFailure("validation_error")
        if request.operation == "generate":
            if request.inputs or request.mask:
                raise ProviderFailure("validation_error")
        elif request.model in {"inpaint", "erase"}:
            if len(request.inputs) != 1 or request.mask is None:
                raise ProviderFailure("validation_error")
        elif len(request.inputs) != 1 or request.mask is not None:
            raise ProviderFailure("validation_error")

    def _fields(self, request: ValidatedRequest) -> dict[str, str]:
        fields: dict[str, str] = {}
        if request.prompt:
            fields["prompt"] = request.prompt
        for key, value in request.normalized_parameters.items():
            if key in {"count", "model"}:
                continue
            fields[key] = str(value).lower() if isinstance(value, bool) else str(value)
        return fields

    def create(self, request: ValidatedRequest) -> ImmediateResult:
        self.validate(request)
        base = self._base()
        try:
            token = self.resolve_credential(self.profile.credential_ref)
            if not token:
                raise ValueError("empty credential")
        except Exception:
            raise ProviderFailure("authentication_error") from None
        files: list[tuple[str, tuple[str | None, bytes | str, str | None]]] = [
            (key, (None, value, None)) for key, value in self._fields(request).items()
        ]
        for index, content in enumerate(request.inputs):
            media_type = validate_image_bytes(content)[1]
            name = "image" if index == 0 else f"image{index}"
            files.append((name, (f"input-{index}.png", content, media_type)))
        if request.mask is not None:
            media_type = validate_image_bytes(request.mask)[1]
            files.append(("mask", ("mask.png", request.mask, media_type)))
        try:
            with client(self.transport, read_timeout=self.read_timeout) as http:
                response = send(
                    http, "POST", base + _ROUTES[request.model][1],
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                    },
                    files=files, max_bytes=self.max_response_bytes,
                )
        except httpx.HTTPError:
            raise ProviderFailure("outcome_unknown", accepted_maybe=True) from None
        if not 200 <= response.status < 300:
            raise failure_for_status(response.status)
        if response.content[:8] == b"\x89PNG\r\n\x1a\n" or response.content[:3] == b"\xff\xd8\xff":
            try:
                content, media_type = validate_image_bytes(response.content)
            except (ValueError, OSError):
                raise ProviderFailure("provider_error", accepted_maybe=True) from None
            return self._result(1, content, media_type, {})
        try:
            payload = json.loads(response.content)
            if payload.get("finish_reason") == "CONTENT_FILTERED":
                raise ProviderFailure("safety_refusal", accepted_maybe=True)
            content, media_type = decode_image(payload["image"])
        except ProviderFailure:
            raise
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            raise ProviderFailure("provider_error", accepted_maybe=True) from None
        return self._result(1, content, media_type, payload)

    @staticmethod
    def _result(
        count: int, content: bytes, media_type: str, payload: dict[str, Any],
    ) -> ImmediateResult:
        from .base import ArtifactDescriptor

        return ImmediateResult(
            requested_count=count, actual_count=1,
            artifacts=(ArtifactDescriptor("art_stability_result", 0, content, media_type),),
            usage={"seed": payload.get("seed")} if "seed" in payload else {},
            private_response=payload,
        )

    def poll(self, handle: object) -> RemoteObservation:
        raise ProviderFailure("unsupported_capability")

    def fetch(self, observation: RemoteObservation) -> ImmediateResult:
        raise ProviderFailure("unsupported_capability")

    def cancel(self, handle: object) -> CancelObservation:
        return CancelObservation("unsupported", "Stability image routes are synchronous")
