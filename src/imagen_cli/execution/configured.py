"""Resolve an explicitly selected profile only when dispatching an approved request."""

import os
import re
from typing import Any

from imagen_cli.application.profile_service import ProfileService
from imagen_cli.providers.base import ImmediateResult, ProviderFailure, ValidatedRequest
from imagen_cli.providers.gemini import GeminiAdapter
from imagen_cli.providers.openai_images import OpenAIImagesAdapter
from imagen_cli.providers.routes import SUPPORTED_ROUTES
from imagen_cli.storage.private_state import PrivateStateStore
from imagen_cli.storage.repositories import Repositories

__all__ = ["ConfiguredAdapter", "SUPPORTED_ROUTES", "resolve_credential"]


def resolve_credential(
    reference: str, *, private_store: Any = None,
) -> str:
    if re.fullmatch(r"env:[A-Za-z_][A-Za-z0-9_]*", reference):
        value = os.environ.get(reference[4:], "")
    elif re.fullmatch(r"private:[A-Za-z][A-Za-z0-9_-]{0,127}", reference):
        if private_store is None:
            raise ValueError("credential backend unavailable")
        try:
            value = private_store.read_secret(reference[8:])
        except (OSError, ValueError) as error:
            raise ValueError("credential unavailable") from error
    elif re.fullmatch(r"keyring:[A-Za-z0-9._-]{1,128}", reference):
        try:
            import keyring
        except ImportError as error:
            raise ValueError("credential backend unavailable") from error
        value = keyring.get_password(reference[8:], "imagen") or ""
    else:
        raise ValueError("unsupported credential reference")
    if not value.strip() or any(char in value for char in "\r\n\0"):
        raise ValueError("credential unavailable")
    return value


class ConfiguredAdapter:
    def __init__(
        self,
        repositories: Repositories,
        *,
        timeout: int = 300,
        private_store: PrivateStateStore | None = None,
    ) -> None:
        self.repositories = repositories
        self.timeout = timeout
        self.private_store = private_store

    def _resolve(self, reference: str) -> str:
        return resolve_credential(reference, private_store=self.private_store)

    def create(self, request: ValidatedRequest) -> ImmediateResult:
        row = self.repositories.get("profiles", request.profile_identity, include_private=True)
        if row is None:
            raise ProviderFailure("validation_error")
        profile = ProfileService().load(row)
        if (profile.provider, profile.family) not in SUPPORTED_ROUTES:
            raise ProviderFailure("unsupported_capability")
        if (profile.provider, profile.family) == ("gemini", "generate_content"):
            if self.private_store is None:
                raise ProviderFailure("unsupported_capability")
            return GeminiAdapter(
                profile,
                private_store=self.private_store,
                resolve_credential=self._resolve,
            ).create(request)
        if (profile.provider, profile.family) != ("openai", "images"):
            raise ProviderFailure("unsupported_capability")
        return OpenAIImagesAdapter(
            profile, resolve_credential=self._resolve, read_timeout=self.timeout,
        ).create(request)
