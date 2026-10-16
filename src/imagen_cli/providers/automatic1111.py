"""AUTOMATIC1111 WebUI API: a named, explicitly self-hosted route (T119)."""

import base64
import json
from collections.abc import Callable
from typing import Any

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

__all__ = ["Automatic1111Adapter"]

_ALLOWED = {
    "count", "size", "seed", "steps", "cfg_scale", "negative_prompt", "sampler_name",
}


class Automatic1111Adapter:
    """The checkpoint is server state; the profile revision, not this adapter, freezes it."""

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
            self.profile.provider != "automatic1111"
            or self.profile.family != "sdapi"
            or request.model != self.profile.model_default
            or request.native_context is not None
            or not request.prompt.strip()
            or not request.model_revision
            or not request.capability_digest
        ):
            raise ProviderFailure("unsupported_capability")
        check_parameters(request.normalized_parameters, _ALLOWED)
        check_inputs(request.inputs, request.mask)
        count = request.normalized_parameters.get("count", 1)
        if type(count) is not int or not 1 <= count <= 8:
            raise ProviderFailure("validation_error")
        if request.operation == "generate" and (request.inputs or request.mask):
            raise ProviderFailure("validation_error")
        if request.operation == "edit" and not request.inputs:
            raise ProviderFailure("validation_error")
        if request.operation not in {"generate", "edit"}:
            raise ProviderFailure("validation_error")

    def _headers(self) -> dict[str, str]:
        try:
            token = self.resolve_credential(self.profile.credential_ref)
        except Exception:
            raise ProviderFailure("authentication_error") from None
        headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _body(self, request: ValidatedRequest) -> dict[str, Any]:
        parameters = dict(request.normalized_parameters)
        body: dict[str, Any] = {"prompt": request.prompt}
        if "count" in parameters:
            body["batch_size"] = parameters["count"]
        size = parameters.get("size")
        if size is not None:
            if not isinstance(size, str) or "x" not in size:
                raise ProviderFailure("validation_error")
            width, _, height = size.partition("x")
            if not width.isdigit() or not height.isdigit():
                raise ProviderFailure("validation_error")
            body["width"], body["height"] = int(width), int(height)
        for key in ("seed", "steps", "cfg_scale", "negative_prompt", "sampler_name"):
            if key in parameters:
                body[key] = parameters[key]
        if request.operation == "edit":
            body["init_images"] = [base64.b64encode(item).decode("ascii")
                                   for item in request.inputs]
            if request.mask is not None:
                body["mask"] = base64.b64encode(request.mask).decode("ascii")
        return body

    def create(self, request: ValidatedRequest) -> ImmediateResult:
        self.validate(request)
        route = "txt2img" if request.operation == "generate" else "img2img"
        try:
            with client(self.transport, read_timeout=self.read_timeout) as http:
                response = send(
                    http, "POST",
                    f"{self.profile.base_url.rstrip('/')}/sdapi/v1/{route}",
                    headers=self._headers(), json=self._body(request),
                    max_bytes=self.max_response_bytes,
                )
        except httpx.HTTPError:
            raise ProviderFailure("outcome_unknown", accepted_maybe=True) from None
        if not 200 <= response.status < 300:
            raise failure_for_status(response.status)
        try:
            payload = json.loads(response.content)
            rows = payload["images"]
            if not isinstance(rows, list) or not rows:
                raise ValueError("no images")
            artifacts: list[ArtifactDescriptor] = []
            for index, encoded in enumerate(rows):
                if not isinstance(encoded, str):
                    raise ValueError("bad image")
                if encoded.startswith("data:"):
                    encoded = encoded.partition(",")[2]
                content, media_type = decode_image(encoded, max_bytes=self.max_image_bytes)
                artifacts.append(ArtifactDescriptor(
                    f"art_a1111_{index}", index, content, media_type,
                ))
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            raise ProviderFailure("provider_error", accepted_maybe=True) from None
        return ImmediateResult(
            requested_count=request.normalized_parameters.get("count", 1),
            actual_count=len(artifacts), artifacts=tuple(artifacts),
            private_response=payload,
        )

    def poll(self, handle: object) -> RemoteObservation:
        raise ProviderFailure("unsupported_capability")

    def fetch(self, observation: RemoteObservation) -> ImmediateResult:
        raise ProviderFailure("unsupported_capability")

    def cancel(self, handle: object = None) -> CancelObservation:
        try:
            with client(self.transport, read_timeout=self.read_timeout) as http:
                response = send(
                    http, "POST",
                    f"{self.profile.base_url.rstrip('/')}/sdapi/v1/interrupt",
                    headers=self._headers(), max_bytes=1024 * 1024,
                )
        except httpx.HTTPError:
            raise ProviderFailure("outcome_unknown") from None
        if not 200 <= response.status < 300:
            raise failure_for_status(response.status)
        return CancelObservation("requested", "WebUI stops the current job; confirmation differs")
