"""OpenAI Images synchronous boundary; accepts inline base64 or result URLs."""

import base64
import binascii
import io
from collections.abc import Callable
from uuid import uuid4

import httpx
from PIL import Image, UnidentifiedImageError

from imagen_cli.domain.models import ProviderProfile
from imagen_cli.storage.download import DownloadError
from imagen_cli.storage.download import fetch as fetch_url

from .base import ArtifactDescriptor, ImmediateResult, ProviderFailure, ValidatedRequest
from .common import validate_image_bytes
from .vocabulary import check, retired, serves


class OpenAIImagesAdapter:
    def __init__(
        self,
        profile: ProviderProfile,
        *,
        resolve_credential: Callable[[str], str],
        transport: httpx.BaseTransport | None = None,
        max_response_bytes: int = 384 * 1024 * 1024,
        max_image_bytes: int = 64 * 1024 * 1024,
        max_pixels: int = 100_000_000,
        read_timeout: float = 60.0,
    ) -> None:
        self.profile = profile.model_copy(deep=True)
        self.resolve_credential = resolve_credential
        self.transport = transport
        self.max_response_bytes = max_response_bytes
        self.max_image_bytes = min(max_image_bytes, 64 * 1024 * 1024)
        self.max_pixels = min(max_pixels, 100_000_000)
        self.read_timeout = read_timeout

    def _validate(self, request: ValidatedRequest) -> None:
        if not request.authorized:
            raise ProviderFailure("approval_required")
        if not request.dispatch_intent_recorded:
            raise ProviderFailure("validation_error")
        if (
            request.profile_identity != self.profile.id
            or request.profile_revision != self.profile.revision
        ):
            raise ProviderFailure("validation_error")
        if (
            retired("openai", "images", request.model)
            or (
                not serves("openai", "images", request.model)
                and request.model != self.profile.model_default
            )
            or request.native_context is not None
        ):
            raise ProviderFailure("unsupported_capability")
        if not request.prompt.strip():
            raise ProviderFailure("validation_error")
        params = request.normalized_parameters
        images = (*request.inputs, *((request.mask,) if request.mask is not None else ()))
        if (
            any(not image or len(image) > self.max_image_bytes for image in images)
            or sum(map(len, images)) > 256 * 1024 * 1024
        ):
            raise ProviderFailure("validation_error")
        if check("openai", "images", request.model, request.operation, params):
            raise ProviderFailure("validation_error")
        if (
            params.get("background") == "transparent"
            and params.get("output_format", "png") == "jpeg"
        ):
            raise ProviderFailure("validation_error")
        if request.operation == "generate":
            if request.inputs or request.mask or "input_fidelity" in params:
                raise ProviderFailure("validation_error")
        elif request.operation == "edit":
            if not request.inputs:
                raise ProviderFailure("validation_error")
        else:
            raise ProviderFailure("validation_error")

    def _request(self, request: ValidatedRequest, token: str) -> httpx.Response:
        params = dict(request.normalized_parameters)
        count = params.pop("count", 1)
        params.pop("input_fidelity", None)
        params = {"model": request.model, "prompt": request.prompt, "n": count, **params}
        route = "generations" if request.operation == "generate" else "edits"
        url = f"{self.profile.base_url.rstrip('/')}/images/{route}"
        headers = {"Authorization": f"Bearer {token}", "Accept-Encoding": "identity"}
        transport = self.transport or httpx.HTTPTransport(retries=0, trust_env=False)
        with httpx.Client(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(60.0, connect=10.0, read=self.read_timeout),
        ) as client:
            if request.operation == "generate":
                outgoing = client.build_request("POST", url, headers=headers, json=params)
            else:
                files = [
                    ("image[]", (f"input-{i}.png", content, "image/png"))
                    for i, content in enumerate(request.inputs)
                ]
                if request.mask is not None:
                    files.append(("mask", ("mask.png", request.mask, "image/png")))
                data = {
                    key: str(value).lower() if isinstance(value, bool) else str(value)
                    for key, value in params.items()
                }
                if "input_fidelity" in request.normalized_parameters:
                    data["input_fidelity"] = str(request.normalized_parameters["input_fidelity"])
                outgoing = client.build_request(
                    "POST", url, headers=headers, data=data, files=files
                )
            response = client.send(outgoing, stream=True)
            try:
                content = bytearray()
                for chunk in response.iter_bytes():
                    if len(chunk) > self.max_response_bytes - len(content):
                        raise ProviderFailure("provider_error", accepted_maybe=True)
                    content.extend(chunk)
                return httpx.Response(response.status_code, content=bytes(content))
            finally:
                response.close()

    def _download(self, url: str) -> tuple[bytes, str]:
        try:
            content = fetch_url(url, max_bytes=self.max_image_bytes)
        except DownloadError as error:
            raise ProviderFailure("provider_error", accepted_maybe=True) from error
        return validate_image_bytes(
            content, max_bytes=self.max_image_bytes, max_pixels=self.max_pixels
        )

    def create(self, request: ValidatedRequest) -> ImmediateResult:
        self._validate(request)
        try:
            token = self.resolve_credential(self.profile.credential_ref)
            response = self._request(request, token)
        except ProviderFailure:
            raise
        except (httpx.HTTPError, OSError):
            raise ProviderFailure("outcome_unknown", accepted_maybe=True) from None
        except Exception:
            raise ProviderFailure("authentication_error") from None
        if response.status_code >= 300:
            if response.status_code in {307, 308}:
                raise ProviderFailure(
                    "outcome_unknown", accepted_maybe=True, http_status=response.status_code
                )
            code = ""
            try:
                code = str(response.json().get("error", {}).get("code", ""))
            except (ValueError, TypeError, AttributeError):
                pass
            kind = {
                400: "validation_error",
                401: "authentication_error",
                403: "permission_denied",
            }.get(response.status_code, "provider_error")
            if code == "content_policy_violation":
                kind = "safety_refusal"
            raise ProviderFailure(
                kind,
                accepted_maybe=response.status_code >= 500,
                http_status=response.status_code,
            )
        if len(response.content) > self.max_response_bytes:
            raise ProviderFailure("provider_error", accepted_maybe=True)
        try:
            body = response.json()
            rows = body["data"]
            if not isinstance(rows, list) or not rows:
                raise ValueError
            artifacts = []
            for index, row in enumerate(rows):
                encoded = row.get("b64_json") if isinstance(row, dict) else None
                if isinstance(encoded, str) and encoded:
                    if len(encoded) > 4 * ((self.max_image_bytes + 2) // 3):
                        raise ValueError
                    content = base64.b64decode(encoded, validate=True)
                    if len(content) > self.max_image_bytes:
                        raise ValueError
                    with Image.open(io.BytesIO(content)) as image:
                        image.verify()
                        if image.width * image.height > self.max_pixels:
                            raise ValueError
                        media_type = Image.MIME.get(image.format or "", "image/png")
                else:
                    url = row.get("url") if isinstance(row, dict) else None
                    if not isinstance(url, str) or not url:
                        raise ValueError
                    content, media_type = self._download(url)
                artifacts.append(
                    ArtifactDescriptor(
                        id=f"art_{uuid4().hex}", index=index, content=content, media_type=media_type
                    )
                )
        except (KeyError, TypeError, ValueError, binascii.Error, UnidentifiedImageError, OSError):
            raise ProviderFailure("provider_error", accepted_maybe=True) from None
        return ImmediateResult(
            requested_count=request.normalized_parameters.get("count", 1),
            actual_count=len(artifacts),
            artifacts=tuple(artifacts),
            usage=body.get("usage", {}),
            private_response=body,
        )
