"""Vertex AI Imagen and Gemini: ADC tokens supplied at call time, explicit region."""

import base64
import importlib
import json
import re
from collections.abc import Callable
from typing import Any
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
    check_inputs,
    check_parameters,
    client,
    decode_image,
    failure_for_status,
    require_authorized,
    require_profile,
    send,
)

__all__ = ["VertexGeminiAdapter", "VertexImagenAdapter", "adc_token"]

_LOCATION = re.compile(r"^[a-z]+(?:-[a-z]+)+[0-9]$")
_PROJECT = re.compile(r"^[a-z][a-z0-9-]{4,62}[a-z0-9]$")
_MEDIA = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp"}
_SAFETY_FINISH = {"SAFETY", "IMAGE_SAFETY", "PROHIBITED_CONTENT"}


def adc_token() -> str:
    """Resolve Application Default Credentials; never called during dry-run planning."""
    try:
        auth = importlib.import_module("google.auth")
        transport = importlib.import_module("google.auth.transport.requests")
    except ImportError:
        raise ProviderFailure("authentication_error") from None
    try:
        credentials, _ = auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        credentials.refresh(transport.Request())
    except Exception:
        raise ProviderFailure("authentication_error") from None
    token = getattr(credentials, "token", None)
    if not isinstance(token, str) or not token:
        raise ProviderFailure("authentication_error")
    return token


class _VertexBase:
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

    def _resource(self, request: ValidatedRequest) -> str:
        project = self.profile.trust_policy.get("project_id")
        location = self.profile.trust_policy.get("location")
        if (
            not isinstance(project, str)
            or _PROJECT.fullmatch(project) is None
            or not isinstance(location, str)
            or _LOCATION.fullmatch(location) is None
        ):
            raise ProviderFailure("validation_error")
        parsed = urlsplit(self.profile.base_url)
        allowed_hosts = {
            "aiplatform.googleapis.com",
            f"{location}-aiplatform.googleapis.com",
        }
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "") not in allowed_hosts
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ProviderFailure("validation_error")
        return (
            f"{self.profile.base_url.rstrip('/')}/v1/projects/{project}/locations/{location}"
            f"/publishers/google/models/{request.model}"
        )

    def _auth(self) -> str:
        try:
            token = self.resolve_credential(self.profile.credential_ref)
            if not token:
                raise ValueError("empty credential")
        except Exception:
            raise ProviderFailure("authentication_error") from None
        return token

    def _post(self, url: str, body: dict[str, Any]) -> Any:
        token = self._auth()
        try:
            with client(self.transport, read_timeout=self.read_timeout) as http:
                response = send(
                    http, "POST", url,
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                             "Accept-Encoding": "identity"},
                    json=body, max_bytes=self.max_response_bytes,
                )
        except httpx.HTTPError:
            raise ProviderFailure("outcome_unknown", accepted_maybe=True) from None
        if not 200 <= response.status < 300:
            raise failure_for_status(response.status)
        try:
            return json.loads(response.content)
        except (ValueError, json.JSONDecodeError):
            raise ProviderFailure("provider_error", accepted_maybe=True) from None

    def poll(self, handle: object) -> RemoteObservation:
        raise ProviderFailure("unsupported_capability")

    def fetch(self, observation: RemoteObservation) -> ImmediateResult:
        raise ProviderFailure("unsupported_capability")

    def cancel(self, handle: object) -> CancelObservation:
        return CancelObservation("unsupported", "Vertex predict/generateContent are synchronous")


class VertexImagenAdapter(_VertexBase):
    def validate(self, request: ValidatedRequest) -> None:
        require_authorized(request)
        require_profile(request, self.profile)
        if (
            self.profile.provider != "vertex"
            or request.operation != "generate"
            or request.native_context is not None
            or not request.prompt.strip()
            or request.inputs
            or request.mask is not None
            or not request.model_revision
            or not request.capability_digest
        ):
            raise ProviderFailure("unsupported_capability")
        check_parameters(request.normalized_parameters, {
            "count", "aspect_ratio", "seed", "output_format", "negative_prompt",
            "add_watermark", "person_generation",
        })
        count = request.normalized_parameters.get("count", 1)
        if type(count) is not int or not 1 <= count <= 4:
            raise ProviderFailure("validation_error")
        if request.normalized_parameters.get("output_format") not in {None, *_MEDIA}:
            raise ProviderFailure("validation_error")
        if request.normalized_parameters.get("aspect_ratio") not in {
            None, "1:1", "3:4", "4:3", "16:9", "9:16",
        }:
            raise ProviderFailure("validation_error")

    def create(self, request: ValidatedRequest) -> ImmediateResult:
        self.validate(request)
        parameters = request.normalized_parameters
        provider_parameters: dict[str, Any] = {
            "sampleCount": parameters.get("count", 1),
        }
        mapping = {
            "aspect_ratio": "aspectRatio",
            "seed": "seed",
            "negative_prompt": "negativePrompt",
            "add_watermark": "addWatermark",
            "person_generation": "personGeneration",
        }
        for key, target in mapping.items():
            if key in parameters:
                provider_parameters[target] = parameters[key]
        if "output_format" in parameters:
            provider_parameters["outputOptions"] = {
                "mimeType": _MEDIA[parameters["output_format"]],
            }
        payload = self._post(
            self._resource(request) + ":predict",
            {"instances": [{"prompt": request.prompt}], "parameters": provider_parameters},
        )
        try:
            predictions = payload["predictions"]
            if not isinstance(predictions, list) or not predictions:
                raise ValueError("no predictions")
            artifacts: list[ArtifactDescriptor] = []
            for index, prediction in enumerate(predictions):
                if prediction.get("raiFilteredReason"):
                    raise ProviderFailure("safety_refusal", accepted_maybe=True)
                content, media_type = decode_image(prediction["bytesBase64Encoded"])
                artifacts.append(
                    ArtifactDescriptor(
                        f"art_vertex_{index}", index, content,
                        prediction.get("mimeType", media_type),
                    )
                )
        except ProviderFailure:
            raise
        except (ValueError, KeyError, TypeError):
            raise ProviderFailure("provider_error", accepted_maybe=True) from None
        return ImmediateResult(
            requested_count=parameters.get("count", 1), actual_count=len(artifacts),
            artifacts=tuple(artifacts), private_response=payload,
        )


class VertexGeminiAdapter(_VertexBase):
    def validate(self, request: ValidatedRequest) -> None:
        require_authorized(request)
        require_profile(request, self.profile)
        if (
            self.profile.provider != "vertex"
            or request.native_context is not None
            or not request.prompt.strip()
            or request.mask is not None
            or not request.model_revision
            or not request.capability_digest
        ):
            raise ProviderFailure("unsupported_capability")
        check_parameters(request.normalized_parameters, {"count", "aspect_ratio"})
        count = request.normalized_parameters.get("count", 1)
        if type(count) is not int or count != 1:
            raise ProviderFailure("validation_error")
        if request.operation == "generate" and request.inputs:
            raise ProviderFailure("validation_error")
        if request.operation == "edit" and not request.inputs:
            raise ProviderFailure("validation_error")
        if request.operation not in {"generate", "edit"}:
            raise ProviderFailure("validation_error")
        check_inputs(request.inputs, request.mask)

    def create(self, request: ValidatedRequest) -> ImmediateResult:
        self.validate(request)
        parts: list[dict[str, Any]] = [{"text": request.prompt}]
        for content in request.inputs:
            media_type = _media_type(content)
            parts.append({"inlineData": {
                "mimeType": media_type,
                "data": base64.b64encode(content).decode("ascii"),
            }})
        generation_config: dict[str, Any] = {"responseModalities": ["TEXT", "IMAGE"]}
        if "aspect_ratio" in request.normalized_parameters:
            generation_config["imageConfig"] = {
                "aspectRatio": request.normalized_parameters["aspect_ratio"],
            }
        payload = self._post(
            self._resource(request) + ":generateContent",
            {"contents": [{"role": "user", "parts": parts}],
             "generationConfig": generation_config},
        )
        try:
            candidates = payload["candidates"]
            if not isinstance(candidates, list) or len(candidates) != 1:
                raise ValueError("unsupported candidates")
            candidate = candidates[0]
            if candidate.get("finishReason") in _SAFETY_FINISH:
                raise ProviderFailure("safety_refusal", accepted_maybe=True)
            parts_out = candidate["content"]["parts"]
            artifacts: list[ArtifactDescriptor] = []
            for part in parts_out:
                inline = part.get("inlineData")
                if not isinstance(inline, dict):
                    continue
                if inline.get("mimeType") not in {"image/png", "image/jpeg", "image/webp"}:
                    raise ValueError("unsupported media")
                content, media_type = decode_image(inline["data"])
                artifacts.append(ArtifactDescriptor(
                    f"art_vertex_{len(artifacts)}", len(artifacts), content, media_type,
                ))
            if not artifacts:
                raise ValueError("no images")
        except ProviderFailure:
            raise
        except (ValueError, KeyError, TypeError):
            raise ProviderFailure("provider_error", accepted_maybe=True) from None
        return ImmediateResult(
            requested_count=1, actual_count=len(artifacts), artifacts=tuple(artifacts),
            private_response=payload,
        )


def _media_type(content: bytes) -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    raise ProviderFailure("validation_error")
