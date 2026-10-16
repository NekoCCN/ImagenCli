"""ComfyUI queue adapter: an explicit workflow is the only supported operation."""

import copy
import json
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

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
    check_parameters,
    client,
    failure_for_status,
    require_authorized,
    require_profile,
    send,
    validate_image_bytes,
)

__all__ = ["ComfyUIAdapter"]


class ComfyUIAdapter:
    """The workflow and prompt node are supplied by the operator; no node is guessed."""

    def __init__(
        self,
        profile: ProviderProfile,
        *,
        resolve_credential: Callable[[str], str],
        workflow: Mapping[str, Any],
        prompt_node: str,
        prompt_input: str = "text",
        transport: httpx.BaseTransport | None = None,
        max_response_bytes: int = 384 * 1024 * 1024,
        max_image_bytes: int = 64 * 1024 * 1024,
        read_timeout: float = 60.0,
    ) -> None:
        self.profile = profile
        self.resolve_credential = resolve_credential
        self.workflow = copy.deepcopy(dict(workflow))
        self.prompt_node = prompt_node
        self.prompt_input = prompt_input
        self.transport = transport
        self.max_response_bytes = min(max_response_bytes, 384 * 1024 * 1024)
        self.max_image_bytes = min(max_image_bytes, 64 * 1024 * 1024)
        self.read_timeout = read_timeout

    def _trusted_url(self, value: str) -> bool:
        parsed = urlsplit(value)
        base = urlsplit(self.profile.base_url)
        return (
            parsed.scheme == "https"
            and parsed.username is None
            and parsed.password is None
            and (parsed.hostname or "").lower() == (base.hostname or "").lower()
        )

    def _headers(self) -> dict[str, str]:
        try:
            token = self.resolve_credential(self.profile.credential_ref)
        except Exception:
            raise ProviderFailure("authentication_error") from None
        headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def validate(self, request: ValidatedRequest) -> None:
        require_authorized(request)
        require_profile(request, self.profile)
        if (
            self.profile.provider != "comfyui"
            or request.operation != "generate"
            or request.model != self.profile.model_default
            or request.inputs
            or request.mask is not None
            or request.native_context is not None
            or not request.prompt.strip()
            or not request.model_revision
            or not request.capability_digest
            or self.prompt_node not in self.workflow
        ):
            raise ProviderFailure("unsupported_capability")
        check_parameters(request.normalized_parameters, {"count"})
        if request.normalized_parameters.get("count", 1) != 1:
            raise ProviderFailure("validation_error")

    def create(self, request: ValidatedRequest) -> RemoteHandle:
        self.validate(request)
        workflow = copy.deepcopy(self.workflow)
        try:
            inputs = workflow[self.prompt_node]["inputs"]
            if not isinstance(inputs, dict):
                raise TypeError("prompt node has no inputs")
            inputs[self.prompt_input] = request.prompt
        except (KeyError, TypeError):
            raise ProviderFailure("validation_error") from None
        try:
            with client(self.transport, read_timeout=self.read_timeout) as http:
                response = send(
                    http, "POST", f"{self.profile.base_url.rstrip('/')}/prompt",
                    headers=self._headers(),
                    json={"prompt": workflow, "client_id": uuid4().hex},
                    max_bytes=self.max_response_bytes,
                )
        except httpx.HTTPError:
            raise ProviderFailure("outcome_unknown", accepted_maybe=True) from None
        if not 200 <= response.status < 300:
            raise failure_for_status(response.status)
        try:
            payload = json.loads(response.content)
            prompt_id = payload["prompt_id"]
            if not isinstance(prompt_id, str) or not prompt_id or payload.get("node_errors"):
                raise ValueError("workflow rejected")
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            raise ProviderFailure("validation_error") from None
        return RemoteHandle(prompt_id, private={"client_id": payload.get("number")})

    def poll(self, handle: RemoteHandle) -> RemoteObservation:
        try:
            with client(self.transport, read_timeout=self.read_timeout) as http:
                response = send(
                    http, "GET",
                    f"{self.profile.base_url.rstrip('/')}/history/{handle.remote_id}",
                    headers=self._headers(), max_bytes=self.max_response_bytes,
                )
        except httpx.HTTPError:
            raise ProviderFailure("provider_error") from None
        if response.status == 404:
            return RemoteObservation("running")
        if not 200 <= response.status < 300:
            raise failure_for_status(response.status)
        try:
            payload = json.loads(response.content)
        except (ValueError, json.JSONDecodeError):
            raise ProviderFailure("provider_error") from None
        entry = payload.get(handle.remote_id) if isinstance(payload, dict) else None
        if not isinstance(entry, dict):
            return RemoteObservation("running")
        status = entry.get("status", {})
        if isinstance(status, dict) and status.get("status_str") == "error":
            return RemoteObservation("failed", private_response=entry)
        if isinstance(status, dict) and status.get("completed") is True:
            return RemoteObservation("succeeded", private_response=entry)
        return RemoteObservation("running", private_response=entry)

    def fetch(self, observation: RemoteObservation) -> ImmediateResult:
        if observation.state != "succeeded":
            raise ProviderFailure("validation_error")
        outputs = observation.private_response.get("outputs")
        if not isinstance(outputs, Mapping):
            raise ProviderFailure("provider_error")
        artifacts: list[ArtifactDescriptor] = []
        for node in outputs.values():
            images = node.get("images") if isinstance(node, Mapping) else None
            if not isinstance(images, (list, tuple)):
                continue
            for image in images:
                if not isinstance(image, Mapping):
                    continue
                filename = image.get("filename")
                if not isinstance(filename, str):
                    continue
                query = urlencode({
                    "filename": filename,
                    "subfolder": str(image.get("subfolder", "")),
                    "type": str(image.get("type", "output")),
                })
                url = f"{self.profile.base_url.rstrip('/')}/view?{query}"
                if not self._trusted_url(url):
                    raise ProviderFailure("provider_error")
                try:
                    with client(self.transport, read_timeout=self.read_timeout) as http:
                        response = send(
                            http, "GET", url, headers=self._headers(),
                            max_bytes=self.max_image_bytes,
                        )
                except httpx.HTTPError:
                    raise ProviderFailure("provider_error") from None
                if not 200 <= response.status < 300:
                    raise failure_for_status(response.status, accepted_maybe=False)
                try:
                    content, media_type = validate_image_bytes(
                        response.content, max_bytes=self.max_image_bytes,
                    )
                except (ValueError, OSError):
                    raise ProviderFailure("provider_error") from None
                artifacts.append(ArtifactDescriptor(
                    f"art_comfyui_{len(artifacts)}", len(artifacts), content, media_type,
                ))
        if not artifacts:
            raise ProviderFailure("provider_error")
        return ImmediateResult(
            requested_count=1, actual_count=len(artifacts), artifacts=tuple(artifacts),
            private_response=observation.private_response,
        )

    def cancel(self, handle: RemoteHandle) -> CancelObservation:
        # Only pending queue entries can be removed; a running prompt needs /interrupt,
        # which would stop an unrelated workflow, so it is deliberately not sent.
        try:
            with client(self.transport, read_timeout=self.read_timeout) as http:
                response = send(
                    http, "POST", f"{self.profile.base_url.rstrip('/')}/queue",
                    headers=self._headers(), json={"delete": [handle.remote_id]},
                    max_bytes=self.max_response_bytes,
                )
        except httpx.HTTPError:
            raise ProviderFailure("outcome_unknown") from None
        if response.status == 404:
            return CancelObservation("not_found")
        if not 200 <= response.status < 300:
            raise failure_for_status(response.status)
        return CancelObservation("requested", "only a pending queue entry is removed")
