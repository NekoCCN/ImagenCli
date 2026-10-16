"""BFL / FLUX asynchronous tasks: submit once, poll, fetch, no invented cancel."""

import base64
import json
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from imagen_cli.domain.models import ProviderProfile

from .base import (
    ArtifactDescriptor,
    CancelObservation,
    ImmediateResult,
    ProviderFailure,
    RemoteHandle,
    RemoteObservation,
    ValidatedRequest,
)
from .common import (
    check_inputs,
    check_parameters,
    client,
    failure_for_status,
    fetch_public_image,
    require_authorized,
    require_profile,
    send,
)

__all__ = ["BFLAdapter"]

_ROUTES = {
    "flux-pro-1.1": "generate",
    "flux-kontext-pro": "edit",
    "flux-pro-1.0-fill": "edit",
}
_ALLOWED = {
    "generate": {"size", "seed", "output_format", "safety_tolerance", "prompt_upsampling"},
    "edit": {
        "size", "seed", "output_format", "safety_tolerance", "prompt_upsampling",
        "guidance", "steps",
    },
}
_SAFETY_STATUSES = {"Content Moderated", "Request Moderated"}
_FAILED_STATUSES = {"Error", "No Card Generated"}
_TRUSTED_HOSTS = {"api.bfl.ai", "api.eu.bfl.ai", "api.us.bfl.ai", "api.bfl.ml"}


class BFLAdapter:
    """Remote task adapter; result delivery is a signed URL, fetched without auth."""

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

    def validate(self, request: ValidatedRequest) -> None:
        require_authorized(request)
        require_profile(request, self.profile)
        if (
            self.profile.provider != "bfl"
            or request.model not in _ROUTES
            or _ROUTES[request.model] != request.operation
            or request.native_context is not None
            or not request.prompt.strip()
            or not request.model_revision
            or not request.capability_digest
        ):
            raise ProviderFailure("unsupported_capability")
        check_parameters(request.normalized_parameters, _ALLOWED[request.operation])
        check_inputs(request.inputs, request.mask)
        if request.operation == "generate" and (request.inputs or request.mask):
            raise ProviderFailure("validation_error")
        if request.model == "flux-kontext-pro" and (
            len(request.inputs) != 1 or request.mask is not None
        ):
            raise ProviderFailure("validation_error")
        if request.model == "flux-pro-1.0-fill" and (
            len(request.inputs) != 1 or request.mask is None
        ):
            raise ProviderFailure("validation_error")
        if request.normalized_parameters.get("output_format") not in {
            None, "jpeg", "png", "webp",
        }:
            raise ProviderFailure("validation_error")
        self._geometry(request.normalized_parameters.get("size"))

    @staticmethod
    def _geometry(size: Any) -> tuple[int, int] | None:
        if size is None:
            return None
        if not isinstance(size, str):
            raise ProviderFailure("validation_error")
        width, separator, height = size.partition("x")
        if not separator or not width.isdigit() or not height.isdigit():
            raise ProviderFailure("validation_error")
        value = (int(width), int(height))
        if any(item < 64 or item > 4096 or item % 32 for item in value):
            raise ProviderFailure("validation_error")
        return value

    def _trusted_url(self, value: str) -> bool:
        parsed = urlsplit(value)
        base = urlsplit(self.profile.base_url)
        host = (parsed.hostname or "").lower()
        return (
            parsed.scheme == "https"
            and parsed.username is None
            and parsed.password is None
            and (host == (base.hostname or "").lower() or host in _TRUSTED_HOSTS)
        )

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "x-key": token,
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        }

    def create(self, request: ValidatedRequest) -> RemoteHandle:
        self.validate(request)
        parameters = dict(request.normalized_parameters)
        size = self._geometry(parameters.pop("size", None))
        body: dict[str, Any] = {"prompt": request.prompt}
        if size is not None:
            body["width"], body["height"] = size
        body.update(parameters)
        if request.model == "flux-kontext-pro":
            body["input_image"] = base64.b64encode(request.inputs[0]).decode("ascii")
        elif request.model == "flux-pro-1.0-fill":
            body["image"] = base64.b64encode(request.inputs[0]).decode("ascii")
            body["mask"] = base64.b64encode(request.mask or b"").decode("ascii")
        try:
            token = self.resolve_credential(self.profile.credential_ref)
            if not token:
                raise ValueError("empty credential")
        except Exception:
            raise ProviderFailure("authentication_error") from None
        try:
            with client(self.transport, read_timeout=self.read_timeout) as http:
                response = send(
                    http, "POST",
                    f"{self.profile.base_url.rstrip('/')}/v1/{request.model}",
                    headers=self._headers(token), json=body, max_bytes=self.max_response_bytes,
                )
        except httpx.HTTPError:
            raise ProviderFailure("outcome_unknown", accepted_maybe=True) from None
        if not 200 <= response.status < 300:
            raise failure_for_status(response.status)
        try:
            payload = json.loads(response.content)
            remote_id = payload["id"]
            if not isinstance(remote_id, str) or not remote_id:
                raise ValueError("missing id")
            polling_url = payload.get("polling_url")
            if polling_url is not None and (
                not isinstance(polling_url, str) or not self._trusted_url(polling_url)
            ):
                raise ValueError("bad polling url")
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            raise ProviderFailure("provider_error", accepted_maybe=True) from None
        return RemoteHandle(remote_id, polling_url=polling_url, private={"body": payload})

    def poll(self, handle: RemoteHandle) -> RemoteObservation:
        try:
            token = self.resolve_credential(self.profile.credential_ref)
            if not token:
                raise ValueError("empty credential")
        except Exception:
            raise ProviderFailure("authentication_error") from None
        url = handle.polling_url or (
            f"{self.profile.base_url.rstrip('/')}/v1/get_result?"
            + urlencode({"id": handle.remote_id})
        )
        if not self._trusted_url(url):
            raise ProviderFailure("validation_error")
        try:
            with client(self.transport, read_timeout=self.read_timeout) as http:
                response = send(
                    http, "GET", url, headers=self._headers(token),
                    max_bytes=self.max_response_bytes,
                )
        except httpx.HTTPError:
            raise ProviderFailure("provider_error") from None
        if not 200 <= response.status < 300:
            raise failure_for_status(response.status)
        try:
            payload = json.loads(response.content)
            status = payload.get("status")
        except (ValueError, TypeError, json.JSONDecodeError):
            raise ProviderFailure("provider_error") from None
        if status in {"Pending", "Reasoning", "Generating"}:
            state = "running"
        elif status == "Ready":
            state = "succeeded"
        elif status in _SAFETY_STATUSES:
            raise ProviderFailure("safety_refusal", accepted_maybe=True)
        elif status in _FAILED_STATUSES:
            state = "failed"
        elif status == "Task not found":
            state = "unknown"
        else:
            state = "unknown"
        return RemoteObservation(state, private_response=payload)

    def fetch(self, observation: RemoteObservation) -> ImmediateResult:
        if observation.state != "succeeded":
            raise ProviderFailure("validation_error")
        try:
            sample = observation.private_response["result"]["sample"]
        except (KeyError, TypeError):
            raise ProviderFailure("provider_error") from None
        if not isinstance(sample, str):
            raise ProviderFailure("provider_error")
        content, media_type = fetch_public_image(
            sample, self.transport, allowed_hosts=("bfl.ai", "bfl.ml", "blackforestlabs.ai"),
        )
        artifact = ArtifactDescriptor("art_bfl_result", 0, content, media_type)
        return ImmediateResult(
            requested_count=1, actual_count=1, artifacts=(artifact,),
            usage={}, private_response=observation.private_response,
        )

    def cancel(self, handle: RemoteHandle) -> CancelObservation:
        # BFL's published surface documents submit, get_result and webhooks, no cancel route.
        return CancelObservation("unsupported", "BFL documents no task cancellation endpoint")

