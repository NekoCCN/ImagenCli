"""Explicit OpenAI-compatible endpoint: declarations decide, the vendor is never assumed."""

import json
from collections.abc import Callable, Mapping

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
    check_inputs,
    check_parameters,
    client,
    decode_image,
    failure_for_status,
    require_authorized,
    require_profile,
    send,
)
from .model_platforms import ModelDeclaration

__all__ = ["OpenAICompatibleAdapter"]


class OpenAICompatibleAdapter:
    """Only the declared model/operation/parameters are forwarded to the explicit base URL."""

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
        declaration = self.declarations.get(request.model)
        if (
            self.profile.provider != "openai-compatible"
            or self.profile.family != "images"
            or declaration is None
            or request.operation not in declaration.operations
            or request.native_context is not None
            or not request.prompt.strip()
            or not request.model_revision
            or not request.capability_digest
        ):
            raise ProviderFailure("unsupported_capability")
        check_parameters(request.normalized_parameters, declaration.allowed_parameters)
        check_inputs(request.inputs, request.mask)
        count = request.normalized_parameters.get("count", 1)
        if type(count) is not int or not 1 <= count <= 10:
            raise ProviderFailure("validation_error")
        if request.operation == "generate" and (request.inputs or request.mask):
            raise ProviderFailure("validation_error")
        if request.operation == "edit" and not request.inputs:
            raise ProviderFailure("validation_error")

    def _auth(self) -> str:
        try:
            token = self.resolve_credential(self.profile.credential_ref)
            if not token:
                raise ValueError("empty credential")
        except Exception:
            raise ProviderFailure("authentication_error") from None
        return token

    def create(self, request: ValidatedRequest) -> ImmediateResult:
        self.validate(request)
        token = self._auth()
        base = self.profile.base_url.rstrip("/")
        parameters = dict(request.normalized_parameters)
        count = parameters.pop("count", 1)
        body = {"model": request.model, "prompt": request.prompt, "n": count, **parameters}
        headers = {
            "Authorization": f"Bearer {token}", "Accept": "application/json",
            "Accept-Encoding": "identity",
        }
        try:
            with client(self.transport, read_timeout=self.read_timeout) as http:
                if request.operation == "generate":
                    response = send(
                        http, "POST", f"{base}/images/generations", headers=headers,
                        json=body, max_bytes=self.max_response_bytes,
                    )
                else:
                    files = [
                        ("image[]", (f"input-{i}.png", content, "image/png"))
                        for i, content in enumerate(request.inputs)
                    ]
                    if request.mask is not None:
                        files.append(("mask", ("mask.png", request.mask, "image/png")))
                    data = {key: str(value) for key, value in body.items()}
                    response = send(
                        http, "POST", f"{base}/images/edits", headers=headers,
                        data=data, files=files, max_bytes=self.max_response_bytes,
                    )
        except httpx.HTTPError:
            raise ProviderFailure("outcome_unknown", accepted_maybe=True) from None
        if not 200 <= response.status < 300:
            code = ""
            try:
                code = str(json.loads(response.content).get("error", {}).get("code", ""))
            except (ValueError, TypeError, AttributeError, json.JSONDecodeError):
                pass
            if code == "content_policy_violation":
                raise ProviderFailure("safety_refusal", accepted_maybe=response.status >= 500)
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
                    # Arbitrary result URLs are not fetched from a compatible endpoint.
                    raise ValueError("inline image required")
                content, media_type = decode_image(encoded, max_bytes=self.max_image_bytes)
                artifacts.append(ArtifactDescriptor(
                    f"art_compatible_{index}", index, content, media_type,
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
        return CancelObservation("unsupported", "the explicit endpoint is synchronous")
