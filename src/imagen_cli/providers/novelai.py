"""Single-attempt NovelAI JSON generation transport. Route registration is separate."""

import base64
import hashlib
import ipaddress
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx

from imagen_cli.domain.models import ProviderProfile

from .base import ImmediateResult, ProviderFailure, ValidatedRequest
from .common import (
    check_inputs,
    client,
    failure_for_status,
    require_authorized,
    require_profile,
    send,
    validate_image_bytes,
)
from .novelai_output import decode_output
from .novelai_parameters import normalize_parameters, validate_request_shape
from .novelai_references import map_references


def _plain(value: Any) -> Any:
    """Thaw immutable request structures for typed validation/JSON encoding."""
    from collections.abc import Mapping

    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


class NovelAIAdapter:
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

    def create(self, request: ValidatedRequest) -> ImmediateResult:
        require_authorized(request)
        require_profile(request, self.profile)
        try:
            endpoint = urlsplit(self.profile.base_url)
            if endpoint.scheme == "http":
                host = endpoint.hostname or ""
                if host != "localhost" and not ipaddress.ip_address(host).is_loopback:
                    raise ValueError
            if (
                request.native_context is not None
                or request.mask is not None
                or not request.prompt.strip()
                or self.profile.provider != "novelai"
                or self.profile.family != "generate_image"
            ):
                raise ValueError
            validate_request_shape(operation=request.operation, primary_count=len(request.inputs),
                                   mask=request.mask, native_context=request.native_context)
            params = normalize_parameters(
                _plain(request.normalized_parameters),
                model=request.model,
                operation=request.operation,
            )
            params.update(map_references(
                request.references,
                mode=(self.profile.novelai_options.reference_transport
                      if self.profile.novelai_options else "encoded"),
                model=request.model, require_approved=True,
            ))
            for reference in request.references:
                if reference.encoding:
                    from .novelai_vibe import identity

                    expected = identity(
                        self.profile,
                        hashlib.sha256(reference.content).hexdigest(),
                        request.model,
                        reference.settings.get("information_extracted", 1),
                    )
                    if dict(reference.encoding.get("identity", {})) != expected:
                        raise ValueError("encoding identity drifted")
            check_inputs(request.inputs, request.mask)
            if request.operation == "edit":
                if len(request.inputs) != 1 or not {"strength", "noise"} <= params.keys():
                    raise ValueError
                validate_image_bytes(request.inputs[0])
                params["image"] = base64.b64encode(request.inputs[0]).decode("ascii")
            elif request.inputs or {"strength", "noise"} & params.keys():
                raise ValueError
        except Exception:
            raise ProviderFailure("validation_error") from None
        try:
            token = self.resolve_credential(self.profile.credential_ref)
            if not token:
                raise ValueError
        except Exception:
            raise ProviderFailure("authentication_error") from None
        mode = self.profile.novelai_options.auth_mode if self.profile.novelai_options else "bearer"
        headers = {"Authorization": f"Bearer {token}"} if mode == "bearer" else {"X-API-Key": token}
        headers.update(
            {"Accept": "application/zip, application/json", "Accept-Encoding": "identity"}
        )
        payload = {
            "input": request.prompt,
            "model": request.model,
            "action": "img2img" if request.operation == "edit" else "generate",
            "parameters": params,
        }
        try:
            with client(self.transport, read_timeout=self.read_timeout) as http:
                response = send(
                    http,
                    "POST",
                    self.profile.base_url.rstrip("/") + "/ai/generate-image",
                    headers=headers,
                    json=payload,
                )
        except ProviderFailure:
            raise
        except (httpx.HTTPError, OSError, ValueError):
            raise ProviderFailure("outcome_unknown", accepted_maybe=True) from None
        if response.status == 429:
            raise ProviderFailure("rate_limited", http_status=429)
        if not 200 <= response.status < 300:
            failure = failure_for_status(response.status)
            raise ProviderFailure(
                failure.type, accepted_maybe=failure.accepted_maybe, http_status=response.status
            )
        return decode_output(
            response.content,
            response.headers.get("content-type", ""),
            requested_count=params["n_samples"],
        )
