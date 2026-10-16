"""OpenAI Responses image tool: stateless, explicit images, never an Images request."""

import json
from collections.abc import Callable

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
    data_url,
    decode_image,
    failure_for_status,
    require_authorized,
    require_profile,
    send,
)

__all__ = ["OpenAIResponsesAdapter"]

_ALLOWED = {
    "count", "size", "quality", "background", "output_format", "output_compression",
    "moderation", "partial_images",
}


class OpenAIResponsesAdapter:
    """The image is an output item of a Responses call; it is not /images/generations."""

    def __init__(
        self,
        profile: ProviderProfile,
        *,
        resolve_credential: Callable[[str], str],
        transport: httpx.BaseTransport | None = None,
        max_response_bytes: int = 384 * 1024 * 1024,
        max_image_bytes: int = 64 * 1024 * 1024,
        read_timeout: float = 60.0,
    ) -> None:
        self.profile = profile
        self.resolve_credential = resolve_credential
        self.transport = transport
        self.max_response_bytes = min(max_response_bytes, 384 * 1024 * 1024)
        self.max_image_bytes = min(max_image_bytes, 64 * 1024 * 1024)
        self.read_timeout = read_timeout

    def validate(self, request: ValidatedRequest) -> None:
        require_authorized(request)
        require_profile(request, self.profile)
        if (
            self.profile.provider != "openai"
            or self.profile.family != "responses"
            or request.model != self.profile.model_default
            or request.native_context is not None
            or request.mask is not None
            or not request.prompt.strip()
            or not request.model_revision
            or not request.capability_digest
        ):
            raise ProviderFailure("unsupported_capability")
        check_parameters(request.normalized_parameters, _ALLOWED)
        check_inputs(request.inputs, request.mask)
        count = request.normalized_parameters.get("count", 1)
        if type(count) is not int or count != 1:
            raise ProviderFailure("unsupported_capability")
        if request.operation == "generate" and request.inputs:
            raise ProviderFailure("validation_error")
        if request.operation == "edit" and not request.inputs:
            raise ProviderFailure("validation_error")
        if request.operation not in {"generate", "edit"}:
            raise ProviderFailure("validation_error")

    def create(self, request: ValidatedRequest) -> ImmediateResult:
        self.validate(request)
        parameters = dict(request.normalized_parameters)
        parameters.pop("count", None)
        tool: dict[str, object] = {"type": "image_generation"}
        tool.update(parameters)
        content: list[dict[str, object]] = [
            {"type": "input_text", "text": request.prompt},
        ]
        content.extend(
            {"type": "input_image", "image_url": data_url(image)}
            for image in request.inputs
        )
        body = {
            "model": request.model,
            "input": [{"role": "user", "content": content}],
            "tools": [tool],
        }
        try:
            token = self.resolve_credential(self.profile.credential_ref)
            if not token:
                raise ValueError("empty credential")
        except Exception:
            raise ProviderFailure("authentication_error") from None
        try:
            with client(self.transport, read_timeout=self.read_timeout) as http:
                response = send(
                    http, "POST", f"{self.profile.base_url.rstrip('/')}/responses",
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                             "Accept-Encoding": "identity"},
                    json=body, max_bytes=self.max_response_bytes,
                )
        except httpx.HTTPError:
            raise ProviderFailure("outcome_unknown", accepted_maybe=True) from None
        if not 200 <= response.status < 300:
            raise failure_for_status(response.status)
        try:
            payload = json.loads(response.content)
            if payload.get("status") in {"failed", "incomplete"}:
                raise ProviderFailure("provider_error", accepted_maybe=True)
            artifacts: list[ArtifactDescriptor] = []
            for item in payload["output"]:
                if not isinstance(item, dict) or item.get("type") != "image_generation_call":
                    continue
                if item.get("status") != "completed":
                    raise ProviderFailure("provider_error", accepted_maybe=True)
                content_bytes, media_type = decode_image(
                    item["result"], max_bytes=self.max_image_bytes,
                )
                artifacts.append(ArtifactDescriptor(
                    f"art_responses_{len(artifacts)}", len(artifacts),
                    content_bytes, media_type,
                ))
            if not artifacts:
                raise ValueError("no image output")
        except ProviderFailure:
            raise
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            raise ProviderFailure("provider_error", accepted_maybe=True) from None
        return ImmediateResult(
            requested_count=1, actual_count=len(artifacts), artifacts=tuple(artifacts),
            usage=payload.get("usage", {}), private_response=payload,
        )

    def poll(self, handle: object) -> RemoteObservation:
        raise ProviderFailure("unsupported_capability")

    def fetch(self, observation: RemoteObservation) -> ImmediateResult:
        raise ProviderFailure("unsupported_capability")

    def cancel(self, handle: object) -> CancelObservation:
        return CancelObservation("unsupported", "the Responses call is synchronous")
