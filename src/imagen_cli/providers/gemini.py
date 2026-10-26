"""Gemini generateContent: bounded single dispatch and opaque private native history."""

import base64
import binascii
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import httpx

from imagen_cli.domain.models import ProviderProfile
from imagen_cli.providers.base import (
    ArtifactDescriptor,
    ImmediateResult,
    NativeContextRef,
    ProviderFailure,
    ValidatedRequest,
)
from imagen_cli.storage.private_state import PrivateStateStore

__all__ = ["GeminiAdapter", "ProviderFailure", "ValidatedRequest"]

# Declared image-model suffixes; a gateway may prefix its own routing segment.
_MODELS = {
    "gemini-2.5-flash-image", "gemini-3-pro-image-preview", "gemini-3.1-flash-image",
    "nano-banana-2",
}


def _trusted_endpoint(base_url: str) -> bool:
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(base_url)
        _ = parts.port
    except ValueError:
        return False
    if (
        not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or not parts.path.rstrip("/").endswith("/v1beta")
    ):
        return False
    if parts.scheme == "https":
        return True
    # Plain HTTP is limited to the loopback gateway, never a remote host.
    return parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost"}


@dataclass(frozen=True)
class ContextRestoration:
    available: bool
    context_loss: str | None = None
    contents: list[dict[str, Any]] = field(default_factory=list, repr=False)


@dataclass(frozen=True)
class GeminiResult(ImmediateResult):
    context: NativeContextRef | None = None


class GeminiAdapter:
    """No SDK, automatic create retries, fallback, or remote-URL fetching."""

    def __init__(
        self,
        profile: ProviderProfile,
        *,
        private_store: PrivateStateStore,
        resolve_credential: Callable[[str], str],
        transport: httpx.BaseTransport | None = None,
        max_response_bytes: int = 384 * 1024 * 1024,
        max_image_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        self.profile = profile
        self.private_store = private_store
        self.resolve_credential = resolve_credential
        self.transport = transport
        self.max_response_bytes = min(max_response_bytes, 384 * 1024 * 1024)
        self.max_image_bytes = min(max_image_bytes, 64 * 1024 * 1024)
        self._attempted: set[str] = set()

    def validate(self, request: ValidatedRequest) -> None:
        if request.authorized is not True:
            raise ProviderFailure("approval_required")
        if request.dispatch_intent_recorded is not True:
            raise ProviderFailure("validation_error")
        if (
            self.profile.provider != "gemini"
            or self.profile.family != "generate_content"
            or request.model.split("/")[-1] not in _MODELS
        ):
            raise ProviderFailure("unsupported_capability")
        if (
            not _trusted_endpoint(self.profile.base_url)
            or request.profile_identity != self.profile.id
            or request.profile_revision != self.profile.revision
            or not request.model_revision
            or not request.capability_digest
            or not request.prompt
            or request.operation not in {"generate", "edit"}
            or request.mask is not None
        ):
            raise ProviderFailure("validation_error")
        parameters = request.normalized_parameters
        if set(parameters) - {"count", "aspect_ratio"}:
            raise ProviderFailure("validation_error")
        count = parameters.get("count", 1)
        if type(count) is not int or count != 1:
            raise ProviderFailure("validation_error")
        if "aspect_ratio" in parameters and parameters["aspect_ratio"] not in {
            "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9",
        }:
            raise ProviderFailure("validation_error")
        if request.operation == "generate" and (request.inputs or request.native_context):
            raise ProviderFailure("validation_error")
        if request.operation == "edit" and not (request.inputs or request.native_context):
            raise ProviderFailure("validation_error")
        if sum(len(image) for image in request.inputs) > 256 * 1024 * 1024:
            raise ProviderFailure("validation_error")
        for image in request.inputs:
            if not image or len(image) > self.max_image_bytes:
                raise ProviderFailure("validation_error")
            self._input_media(image)

    @staticmethod
    def _input_media(image: bytes) -> str:
        if image.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if image.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if image.startswith(b"RIFF") and image[8:12] == b"WEBP":
            return "image/webp"
        raise ProviderFailure("validation_error")

    def restore_context(
        self, context: NativeContextRef, request: ValidatedRequest,
    ) -> ContextRestoration:
        """Local only; loss never silently switches to explicit-image continuation."""
        try:
            state = json.loads(self.private_store.read(context.identity, context.sha256))
            if (
                state["format_version"] != 1
                or state["provider"] != "gemini"
                or state["family"] != self.profile.family
                or state["profile_identity"] != request.profile_identity
                or state["profile_identity"] != self.profile.id
                or state["profile_revision"] != request.profile_revision
                or state["profile_revision"] != self.profile.revision
                or state["model"] != request.model
                or state["model_revision"] != request.model_revision
                or state["expires_at"] is not None
                or not isinstance(state["contents"], list)
                or not state["contents"]
            ):
                return ContextRestoration(False, "native_context_incompatible")
            return ContextRestoration(True, contents=state["contents"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return ContextRestoration(False, "native_context_unavailable")

    def create(self, request: ValidatedRequest) -> GeminiResult:
        self.validate(request)
        contents: list[dict[str, Any]] = []
        if request.native_context is not None:
            restored = self.restore_context(request.native_context, request)
            if not restored.available:
                raise ProviderFailure("context_loss")
            contents = restored.contents
        parts: list[dict[str, Any]] = [{"text": request.prompt}]
        parts.extend({"inlineData": {
            "mimeType": self._input_media(image), "data": base64.b64encode(image).decode("ascii"),
        }} for image in request.inputs)
        contents = [*contents, {"role": "user", "parts": parts}]
        config: dict[str, Any] = {"responseModalities": ["TEXT", "IMAGE"]}
        if "aspect_ratio" in request.normalized_parameters:
            config["imageConfig"] = {"aspectRatio": request.normalized_parameters["aspect_ratio"]}
        body = {"contents": contents, "generationConfig": config}
        if request.job_id in self._attempted:
            raise ProviderFailure("outcome_unknown", accepted_maybe=True)
        try:
            credential = self.resolve_credential(self.profile.credential_ref)
            if not credential:
                raise ValueError("empty credential")
        except Exception:
            raise ProviderFailure("authentication_error") from None
        self._attempted.add(request.job_id)
        try:
            with httpx.Client(
                transport=self.transport or httpx.HTTPTransport(retries=0, trust_env=False),
                trust_env=False, follow_redirects=False,
                timeout=httpx.Timeout(60, connect=10),
            ) as client:
                with client.stream(
                    "POST",
                    f"{self.profile.base_url.rstrip('/')}/models/{request.model}:generateContent",
                    headers={"x-goog-api-key": credential}, json=body,
                ) as response:
                    self._check_status(response.status_code)
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        if len(raw) + len(chunk) > self.max_response_bytes:
                            raise ProviderFailure("provider_error", accepted_maybe=True)
                        raw.extend(chunk)
        except httpx.HTTPError:
            raise ProviderFailure("outcome_unknown", accepted_maybe=True) from None
        try:
            payload = json.loads(raw)
            artifacts, native_content = self._extract(payload)
        except (ValueError, KeyError, TypeError, AttributeError, binascii.Error):
            raise ProviderFailure("provider_error", accepted_maybe=True) from None
        state = {
            "format_version": 1, "provider": "gemini", "family": self.profile.family,
            "profile_identity": request.profile_identity,
            "profile_revision": request.profile_revision,
            "model": request.model, "model_revision": request.model_revision, "expires_at": None,
            "contents": [*contents, native_content], "private_response": payload,
        }
        identity = "ctx_" + uuid4().hex
        try:
            metadata = self.private_store.write(
                identity, json.dumps(state, ensure_ascii=False, allow_nan=False).encode("utf-8"),
            )
        except (OSError, ValueError):
            raise ProviderFailure("save_failed", accepted_maybe=True) from None
        return GeminiResult(
            requested_count=1, actual_count=len(artifacts), artifacts=tuple(artifacts),
            usage=payload.get("usageMetadata", {}), private_response=payload,
            context=NativeContextRef(identity, metadata["sha256"]),
        )

    @staticmethod
    def _check_status(status: int) -> None:
        if 200 <= status < 300:
            return
        kind = {
            400: "validation_error", 401: "authentication_error", 403: "permission_denied",
        }.get(status, "outcome_unknown" if 300 <= status < 400 else "provider_error")
        raise ProviderFailure(kind, accepted_maybe=300 <= status < 400 or status >= 500)

    def _extract(self, payload: Any) -> tuple[list[ArtifactDescriptor], dict[str, Any]]:
        if payload.get("promptFeedback", {}).get("blockReason"):
            raise ProviderFailure("safety_refusal", accepted_maybe=True)
        candidates = payload["candidates"]
        if not isinstance(candidates, list) or len(candidates) != 1:
            raise ValueError("unsupported candidates")
        candidate = candidates[0]
        if candidate.get("finishReason") in {"SAFETY", "IMAGE_SAFETY", "PROHIBITED_CONTENT"}:
            raise ProviderFailure("safety_refusal", accepted_maybe=True)
        content = candidate["content"]
        if not isinstance(content["parts"], list):
            raise ValueError("malformed parts")
        artifacts: list[ArtifactDescriptor] = []
        for part in content["parts"]:
            if "inlineData" not in part:
                continue
            image = part["inlineData"]
            if image["mimeType"] not in {"image/png", "image/jpeg", "image/webp"}:
                raise ValueError("unsupported media")
            encoded = image["data"]
            if not isinstance(encoded, str) or len(encoded) > 4 * ((self.max_image_bytes + 2) // 3):
                raise ValueError("image limit")
            data = base64.b64decode(encoded, validate=True)
            if not data or len(data) > self.max_image_bytes:
                raise ValueError("image limit")
            artifacts.append(ArtifactDescriptor(
                id="art_" + uuid4().hex, index=len(artifacts), content=data,
                media_type=image["mimeType"],
            ))
        if not artifacts:
            raise ValueError("no images")
        return artifacts, content
