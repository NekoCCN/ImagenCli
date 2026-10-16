"""OpenRouter image API: declared model ids, no inherited original-vendor capabilities."""

import json
from collections.abc import Callable, Mapping
from urllib.parse import urlsplit

import httpx

from imagen_cli.domain.models import ProviderProfile

from .base import (
    ArtifactDescriptor,
    CancelObservation,
    ImmediateResult,
    ProviderFailure,
    RemoteObservation,
    ValidatedRequest,
)
from .common import (
    check_parameters,
    client,
    decode_image,
    failure_for_status,
    require_authorized,
    require_profile,
    send,
)
from .model_platforms import ModelDeclaration, resolve_model_id

__all__ = ["OpenRouterAdapter"]


class OpenRouterAdapter:
    """A routing endpoint is not evidence that every routed model accepts the request."""

    def __init__(
        self,
        profile: ProviderProfile,
        *,
        resolve_credential: Callable[[str], str],
        declarations: Mapping[str, ModelDeclaration] | None = None,
        transport: httpx.BaseTransport | None = None,
        max_response_bytes: int = 384 * 1024 * 1024,
        max_image_bytes: int = 64 * 1024 * 1024,
        read_timeout: float = 60.0,
    ) -> None:
        self.profile = profile
        self.resolve_credential = resolve_credential
        self.declarations = dict(declarations or {})
        self.transport = transport
        self.max_response_bytes = min(max_response_bytes, 384 * 1024 * 1024)
        self.max_image_bytes = min(max_image_bytes, 64 * 1024 * 1024)
        self.read_timeout = read_timeout

    def validate(self, request: ValidatedRequest) -> None:
        require_authorized(request)
        require_profile(request, self.profile)
        try:
            model = resolve_model_id(request.model)
        except ValueError:
            raise ProviderFailure("validation_error") from None
        declaration = self.declarations.get(model)
        parsed = urlsplit(self.profile.base_url)
        if (
            self.profile.provider != "openrouter"
            or self.profile.family != "images"
            or parsed.scheme != "https"
            or parsed.hostname != "openrouter.ai"
            or declaration is None
            or request.operation != "generate"
            or request.inputs
            or request.mask is not None
            or request.native_context is not None
            or not request.prompt.strip()
            or not request.model_revision
            or not request.capability_digest
        ):
            raise ProviderFailure("unsupported_capability")
        check_parameters(request.normalized_parameters, declaration.allowed_parameters)
        count = request.normalized_parameters.get("count", 1)
        if type(count) is not int or not 1 <= count <= 10:
            raise ProviderFailure("validation_error")

    def create(self, request: ValidatedRequest) -> ImmediateResult:
        self.validate(request)
        try:
            token = self.resolve_credential(self.profile.credential_ref)
            if not token:
                raise ValueError("empty credential")
        except Exception:
            raise ProviderFailure("authentication_error") from None
        parameters = dict(request.normalized_parameters)
        count = parameters.pop("count", 1)
        body = {"model": request.model, "prompt": request.prompt, "n": count, **parameters}
        try:
            with client(self.transport, read_timeout=self.read_timeout) as http:
                response = send(
                    http, "POST", f"{self.profile.base_url.rstrip('/')}/api/v1/images",
                    headers={
                        "Authorization": f"Bearer {token}", "Accept": "application/json",
                        "Accept-Encoding": "identity",
                    },
                    json=body, max_bytes=self.max_response_bytes,
                )
        except httpx.HTTPError:
            raise ProviderFailure("outcome_unknown", accepted_maybe=True) from None
        if not 200 <= response.status < 300:
            raise failure_for_status(response.status)
        try:
            payload = json.loads(response.content)
            rows = payload["data"]
            if not isinstance(rows, list) or not rows:
                raise ValueError("no images")
            artifacts: list[ArtifactDescriptor] = []
            for index, row in enumerate(rows):
                encoded = row.get("b64_json") if isinstance(row, dict) else None
                if not isinstance(encoded, str):
                    raise ValueError("inline image required")
                content, media_type = decode_image(encoded, max_bytes=self.max_image_bytes)
                declared = row.get("media_type")
                media = declared if declared in {"image/png", "image/jpeg", "image/webp"} else (
                    media_type
                )
                artifacts.append(ArtifactDescriptor(
                    f"art_openrouter_{index}", index, content, str(media),
                ))
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            raise ProviderFailure("provider_error", accepted_maybe=True) from None
        return ImmediateResult(
            requested_count=count, actual_count=len(artifacts),
            artifacts=tuple(artifacts), usage=payload.get("usage", {}),
            private_response=payload,
        )

    def poll(self, handle: object) -> RemoteObservation:
        raise ProviderFailure("unsupported_capability")

    def fetch(self, observation: RemoteObservation) -> ImmediateResult:
        raise ProviderFailure("unsupported_capability")

    def cancel(self, handle: object) -> CancelObservation:
        return CancelObservation("unsupported", "the Images API is a synchronous response")
