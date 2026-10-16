"""fal.ai and Replicate model platforms: explicit model ids and declaration-gated calls."""

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

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
    data_url,
    decode_image,
    failure_for_status,
    fetch_public_image,
    require_authorized,
    require_profile,
    send,
)

__all__ = [
    "FalAdapter", "ModelDeclaration", "ReplicateAdapter", "ReplicateModel", "resolve_model_id",
]

_MODEL_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*){1,3}$"
)
_FAL_HOSTS = {"queue.fal.run", "queue.falrun.com"}
_FAL_MEDIA_HOSTS = ("fal.media", "fal.run", "fal.ai")
_REPLICATE_MEDIA_HOSTS = ("replicate.delivery", "replicate.com")


@dataclass(frozen=True)
class ModelDeclaration:
    """Only declared operations and input/output fields are executable."""

    operations: frozenset[str]
    allowed_parameters: frozenset[str] = frozenset()
    prompt_field: str = "prompt"
    image_field: str | None = None
    images_field: str | None = None
    mask_field: str | None = None
    parameter_fields: Mapping[str, str] = field(default_factory=dict)
    output_path: tuple[str, ...] = ("images",)


def resolve_model_id(identifier: str) -> str:
    """Resolve an owner/name endpoint id; never accept a URL or an injected path."""
    value = identifier.strip().strip("/")
    if not _MODEL_PATTERN.fullmatch(value):
        raise ValueError("invalid model identifier")
    return value


@dataclass(frozen=True)
class ReplicateModel:
    owner: str
    name: str
    version: str | None = None

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"


def resolve_replicate_model(identifier: str) -> ReplicateModel:
    slug, separator, version = identifier.strip().partition(":")
    owner, slash, name = slug.partition("/")
    if (
        not slash
        or not owner
        or not name
        or "/" in name
        or not re.fullmatch(r"[A-Za-z0-9._-]+", owner)
        or not re.fullmatch(r"[A-Za-z0-9._-]+", name)
        or (separator and re.fullmatch(r"[A-Za-z0-9]{16,64}", version) is None)
    ):
        raise ValueError("invalid Replicate model identifier")
    return ReplicateModel(owner, name, version or None)


def _apply_parameters(
    parameters: Mapping[str, Any], declaration: ModelDeclaration,
) -> dict[str, Any]:
    return {
        declaration.parameter_fields.get(key, key): value
        for key, value in parameters.items()
    }


def _outputs(payload: Any, path: tuple[str, ...]) -> list[Any]:
    value: Any = payload
    for key in path:
        if not isinstance(value, Mapping) or key not in value:
            raise ProviderFailure("provider_error")
        value = value[key]
    if isinstance(value, list):
        return list(value)
    if isinstance(value, (str, Mapping)):
        return [value]
    raise ProviderFailure("provider_error")


def _collect_items(
    items: list[Any], transport: httpx.BaseTransport | None, hosts: tuple[str, ...],
) -> tuple[ArtifactDescriptor, ...]:
    artifacts: list[ArtifactDescriptor] = []
    for item in items:
        if isinstance(item, str):
            content, media_type = fetch_public_image(item, transport, allowed_hosts=hosts)
        elif isinstance(item, Mapping):
            encoded = item.get("b64_json") or item.get("base64")
            if isinstance(encoded, str):
                content, media_type = decode_image(encoded)
            elif isinstance(item.get("url"), str):
                content, media_type = fetch_public_image(
                    item["url"], transport, allowed_hosts=hosts,
                )
            else:
                raise ProviderFailure("provider_error")
        else:
            raise ProviderFailure("provider_error")
        artifacts.append(ArtifactDescriptor(
            f"art_platform_{len(artifacts)}", len(artifacts), content, media_type,
        ))
    if not artifacts:
        raise ProviderFailure("provider_error")
    return tuple(artifacts)


class FalAdapter:
    """Queue API: submit once, poll status only, fetch the stored response, cancel by URL."""

    def __init__(
        self,
        profile: ProviderProfile,
        *,
        resolve_credential: Callable[[str], str],
        declarations: Mapping[str, ModelDeclaration] | None = None,
        transport: httpx.BaseTransport | None = None,
        max_response_bytes: int = 384 * 1024 * 1024,
        read_timeout: float = 60.0,
    ) -> None:
        self.profile = profile
        self.resolve_credential = resolve_credential
        self.declarations = dict(declarations or {})
        self.transport = transport
        self.max_response_bytes = min(max_response_bytes, 384 * 1024 * 1024)
        self.read_timeout = read_timeout

    def _declaration(self, request: ValidatedRequest) -> ModelDeclaration:
        try:
            model = resolve_model_id(request.model)
        except ValueError:
            raise ProviderFailure("validation_error") from None
        declaration = self.declarations.get(model)
        if (
            self.profile.provider != "fal"
            or declaration is None
            or request.operation not in declaration.operations
        ):
            raise ProviderFailure("unsupported_capability")
        return declaration

    def validate(self, request: ValidatedRequest) -> None:
        require_authorized(request)
        require_profile(request, self.profile)
        declaration = self._declaration(request)
        if (
            request.native_context is not None
            or not request.prompt.strip()
            or not request.model_revision
            or not request.capability_digest
        ):
            raise ProviderFailure("validation_error")
        check_parameters(request.normalized_parameters, declaration.allowed_parameters)
        check_inputs(request.inputs, request.mask)
        if request.inputs:
            if declaration.image_field is None and declaration.images_field is None:
                raise ProviderFailure("unsupported_capability")
            if len(request.inputs) > 1 and declaration.images_field is None:
                raise ProviderFailure("unsupported_capability")
        if request.mask is not None and declaration.mask_field is None:
            raise ProviderFailure("unsupported_capability")

    def _body(self, request: ValidatedRequest, declaration: ModelDeclaration) -> dict[str, Any]:
        body: dict[str, Any] = {declaration.prompt_field: request.prompt}
        if declaration.image_field is not None and request.inputs:
            body[declaration.image_field] = data_url(request.inputs[0])
        if declaration.images_field is not None and len(request.inputs) > 1:
            body[declaration.images_field] = [data_url(item) for item in request.inputs]
        if declaration.mask_field is not None and request.mask is not None:
            body[declaration.mask_field] = data_url(request.mask)
        body.update(_apply_parameters(request.normalized_parameters, declaration))
        return body

    def _auth(self) -> str:
        try:
            token = self.resolve_credential(self.profile.credential_ref)
            if not token:
                raise ValueError("empty credential")
        except Exception:
            raise ProviderFailure("authentication_error") from None
        return token

    def create(self, request: ValidatedRequest) -> RemoteHandle:
        self.validate(request)
        declaration = self._declaration(request)
        model = resolve_model_id(request.model)
        token = self._auth()
        try:
            with client(self.transport, read_timeout=self.read_timeout) as http:
                response = send(
                    http, "POST",
                    f"{self.profile.base_url.rstrip('/')}/{model}",
                    headers={
                        "Authorization": f"Key {token}",
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                    },
                    json=self._body(request, declaration), max_bytes=self.max_response_bytes,
                )
        except httpx.HTTPError:
            raise ProviderFailure("outcome_unknown", accepted_maybe=True) from None
        if not 200 <= response.status < 300:
            raise failure_for_status(response.status)
        try:
            payload = json.loads(response.content)
            request_id = payload["request_id"]
            status_url = payload.get("status_url")
            response_url = payload.get("response_url")
            cancel_url = payload.get("cancel_url")
            if not isinstance(request_id, str) or not request_id:
                raise ValueError("missing request id")
            for value in (status_url, response_url, cancel_url):
                if value is not None and not self._trusted_url(value):
                    raise ValueError("untrusted queue url")
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            raise ProviderFailure("provider_error", accepted_maybe=True) from None
        return RemoteHandle(
            request_id, polling_url=status_url, cancel_url=cancel_url,
            private={"response_url": response_url, "model": model},
        )

    def _trusted_url(self, value: str) -> bool:
        parsed = urlsplit(value)
        base = urlsplit(self.profile.base_url)
        host = (parsed.hostname or "").lower()
        return (
            parsed.scheme == "https"
            and parsed.username is None
            and parsed.password is None
            and (host == (base.hostname or "").lower() or host in _FAL_HOSTS)
        )

    def poll(self, handle: RemoteHandle) -> RemoteObservation:
        token = self._auth()
        url = handle.polling_url
        if url is None or not self._trusted_url(url):
            raise ProviderFailure("validation_error")
        try:
            with client(self.transport, read_timeout=self.read_timeout) as http:
                response = send(
                    http, "GET", url,
                    headers={"Authorization": f"Key {token}", "Accept": "application/json",
                             "Accept-Encoding": "identity"},
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
        state = {
            "IN_QUEUE": "queued", "IN_PROGRESS": "running", "COMPLETED": "succeeded",
        }.get(status, "unknown")
        response_url = payload.get("response_url") or handle.private.get("response_url")
        if response_url is not None and not self._trusted_url(str(response_url)):
            raise ProviderFailure("provider_error")
        return RemoteObservation(
            state,
            private_response={
                **payload, "response_url": response_url, "model": handle.private.get("model"),
            },
        )

    def fetch(self, observation: RemoteObservation) -> ImmediateResult:
        if observation.state != "succeeded":
            raise ProviderFailure("validation_error")
        url = observation.private_response.get("response_url")
        if not isinstance(url, str) or not self._trusted_url(url):
            raise ProviderFailure("provider_error")
        token = self._auth()
        try:
            with client(self.transport, read_timeout=self.read_timeout) as http:
                response = send(
                    http, "GET", url,
                    headers={"Authorization": f"Key {token}", "Accept": "application/json",
                             "Accept-Encoding": "identity"},
                    max_bytes=self.max_response_bytes,
                )
        except httpx.HTTPError:
            raise ProviderFailure("provider_error") from None
        if not 200 <= response.status < 300:
            raise failure_for_status(response.status)
        try:
            payload = json.loads(response.content)
            model_id = resolve_model_id(str(observation.private_response.get("model", "")))
            declaration = self.declarations[model_id]
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            raise ProviderFailure("provider_error") from None
        artifacts = _collect_items(
            _outputs(payload, declaration.output_path), self.transport, _FAL_MEDIA_HOSTS,
        )
        return ImmediateResult(
            requested_count=len(artifacts), actual_count=len(artifacts),
            artifacts=artifacts, private_response=payload,
        )

    def cancel(self, handle: RemoteHandle) -> CancelObservation:
        token = self._auth()
        url = handle.cancel_url
        if url is None or not self._trusted_url(url):
            raise ProviderFailure("validation_error")
        try:
            with client(self.transport, read_timeout=self.read_timeout) as http:
                response = send(
                    http, "PUT", url,
                    headers={"Authorization": f"Key {token}", "Accept": "application/json",
                             "Accept-Encoding": "identity"},
                    max_bytes=self.max_response_bytes,
                )
        except httpx.HTTPError:
            raise ProviderFailure("outcome_unknown") from None
        try:
            payload = json.loads(response.content)
            status = payload.get("status")
        except (ValueError, TypeError, json.JSONDecodeError):
            raise ProviderFailure("provider_error") from None
        state = {
            "OK": "removed", "CANCELLATION_REQUESTED": "requested",
            "ALREADY_COMPLETED": "already_completed", "NOT_FOUND": "not_found",
        }.get(status, "unknown")
        return CancelObservation(state)


class ReplicateAdapter:
    """Predictions API: version or owner/name route, poll, fetch outputs, cancel."""

    def __init__(
        self,
        profile: ProviderProfile,
        *,
        resolve_credential: Callable[[str], str],
        declarations: Mapping[str, ModelDeclaration] | None = None,
        transport: httpx.BaseTransport | None = None,
        max_response_bytes: int = 384 * 1024 * 1024,
        read_timeout: float = 60.0,
    ) -> None:
        self.profile = profile
        self.resolve_credential = resolve_credential
        self.declarations = dict(declarations or {})
        self.transport = transport
        self.max_response_bytes = min(max_response_bytes, 384 * 1024 * 1024)
        self.read_timeout = read_timeout

    def _declaration(self, request: ValidatedRequest) -> tuple[ReplicateModel, ModelDeclaration]:
        try:
            model = resolve_replicate_model(request.model)
        except ValueError:
            raise ProviderFailure("validation_error") from None
        declaration = self.declarations.get(model.slug)
        if (
            self.profile.provider != "replicate"
            or declaration is None
            or request.operation not in declaration.operations
        ):
            raise ProviderFailure("unsupported_capability")
        return model, declaration

    def validate(self, request: ValidatedRequest) -> None:
        require_authorized(request)
        require_profile(request, self.profile)
        _, declaration = self._declaration(request)
        if (
            request.native_context is not None
            or not request.prompt.strip()
            or not request.model_revision
            or not request.capability_digest
        ):
            raise ProviderFailure("validation_error")
        check_parameters(request.normalized_parameters, declaration.allowed_parameters)
        check_inputs(request.inputs, request.mask)
        if request.inputs:
            if declaration.image_field is None and declaration.images_field is None:
                raise ProviderFailure("unsupported_capability")
            if len(request.inputs) > 1 and declaration.images_field is None:
                raise ProviderFailure("unsupported_capability")
        if request.mask is not None and declaration.mask_field is None:
            raise ProviderFailure("unsupported_capability")

    def _body(self, request: ValidatedRequest, declaration: ModelDeclaration) -> dict[str, Any]:
        body: dict[str, Any] = {declaration.prompt_field: request.prompt}
        if declaration.image_field is not None and request.inputs:
            body[declaration.image_field] = data_url(request.inputs[0])
        if declaration.images_field is not None and len(request.inputs) > 1:
            body[declaration.images_field] = [data_url(item) for item in request.inputs]
        if declaration.mask_field is not None and request.mask is not None:
            body[declaration.mask_field] = data_url(request.mask)
        body.update(_apply_parameters(request.normalized_parameters, declaration))
        return body

    def _auth(self) -> str:
        try:
            token = self.resolve_credential(self.profile.credential_ref)
            if not token:
                raise ValueError("empty credential")
        except Exception:
            raise ProviderFailure("authentication_error") from None
        return token

    def create(self, request: ValidatedRequest) -> RemoteHandle:
        self.validate(request)
        model, declaration = self._declaration(request)
        token = self._auth()
        base = self.profile.base_url.rstrip("/")
        payload: dict[str, Any] = {"input": self._body(request, declaration)}
        if model.version is not None:
            url = f"{base}/v1/predictions"
            payload["version"] = model.version
        else:
            url = f"{base}/v1/models/{model.slug}/predictions"
        try:
            with client(self.transport, read_timeout=self.read_timeout) as http:
                response = send(
                    http, "POST", url,
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                             "Accept-Encoding": "identity"},
                    json=payload, max_bytes=self.max_response_bytes,
                )
        except httpx.HTTPError:
            raise ProviderFailure("outcome_unknown", accepted_maybe=True) from None
        if not 200 <= response.status < 300:
            raise failure_for_status(response.status)
        try:
            body = json.loads(response.content)
            remote_id = body["id"]
            if not isinstance(remote_id, str) or not remote_id:
                raise ValueError("missing prediction id")
            urls = body.get("urls", {})
            get_url, cancel_url = urls.get("get"), urls.get("cancel")
            if not self._trusted_url(get_url):
                raise ValueError("untrusted prediction url")
            if cancel_url is not None and not self._trusted_url(cancel_url):
                raise ValueError("untrusted cancel url")
        except (ValueError, KeyError, TypeError, AttributeError, json.JSONDecodeError):
            raise ProviderFailure("provider_error", accepted_maybe=True) from None
        return RemoteHandle(
            remote_id, polling_url=get_url, cancel_url=cancel_url,
            private={"model": model.slug},
        )

    def _trusted_url(self, value: Any) -> bool:
        if not isinstance(value, str):
            return False
        parsed = urlsplit(value)
        base = urlsplit(self.profile.base_url)
        return (
            parsed.scheme == "https"
            and parsed.username is None
            and parsed.password is None
            and (parsed.hostname or "").lower() == (base.hostname or "").lower()
        )

    def poll(self, handle: RemoteHandle) -> RemoteObservation:
        token = self._auth()
        if handle.polling_url is None or not self._trusted_url(handle.polling_url):
            raise ProviderFailure("validation_error")
        try:
            with client(self.transport, read_timeout=self.read_timeout) as http:
                response = send(
                    http, "GET", handle.polling_url,
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                             "Accept-Encoding": "identity"},
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
        if status in {"starting", "processing"}:
            state = "running"
        elif status == "succeeded":
            state = "succeeded"
        elif status == "failed":
            state = "failed"
        elif status == "canceled":
            state = "canceled"
        else:
            state = "unknown"
        return RemoteObservation(
            state,
            private_response={**payload, "model": handle.private.get("model")},
        )

    def fetch(self, observation: RemoteObservation) -> ImmediateResult:
        if observation.state != "succeeded":
            raise ProviderFailure("validation_error")
        slug = observation.private_response.get("model")
        declaration = self.declarations.get(str(slug)) if slug is not None else None
        if declaration is None:
            raise ProviderFailure("provider_error")
        try:
            payload = observation.private_response
            artifacts = _collect_items(
                _outputs(payload, declaration.output_path), self.transport, _REPLICATE_MEDIA_HOSTS,
            )
        except (ProviderFailure, ValueError, TypeError):
            raise ProviderFailure("provider_error") from None
        return ImmediateResult(
            requested_count=len(artifacts), actual_count=len(artifacts),
            artifacts=artifacts, private_response=payload,
        )

    def cancel(self, handle: RemoteHandle) -> CancelObservation:
        token = self._auth()
        if handle.cancel_url is None or not self._trusted_url(handle.cancel_url):
            raise ProviderFailure("validation_error")
        try:
            with client(self.transport, read_timeout=self.read_timeout) as http:
                response = send(
                    http, "POST", handle.cancel_url,
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                             "Accept-Encoding": "identity"},
                    max_bytes=self.max_response_bytes,
                )
        except httpx.HTTPError:
            raise ProviderFailure("outcome_unknown") from None
        if response.status == 404:
            return CancelObservation("not_found")
        if not 200 <= response.status < 300:
            raise failure_for_status(response.status)
        try:
            payload = json.loads(response.content)
            status = payload.get("status")
        except (ValueError, TypeError, json.JSONDecodeError):
            status = None
        if status == "canceled":
            return CancelObservation("canceled")
        return CancelObservation("requested")
