"""Resolve an explicitly selected profile only when dispatching an approved request."""

import os
import re

from imagen_cli.application.profile_service import ProfileService
from imagen_cli.providers.base import ImmediateResult, ProviderFailure, ValidatedRequest
from imagen_cli.providers.gemini import GeminiAdapter
from imagen_cli.providers.openai_images import OpenAIImagesAdapter
from imagen_cli.storage.private_state import PrivateStateStore
from imagen_cli.storage.repositories import Repositories


def resolve_credential(reference: str) -> str:
    if re.fullmatch(r"env:[A-Za-z_][A-Za-z0-9_]*", reference) is None:
        raise ValueError("unsupported credential reference")
    value = os.environ.get(reference[4:], "")
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

    def create(self, request: ValidatedRequest) -> ImmediateResult:
        row = self.repositories.get("profiles", request.profile_identity, include_private=True)
        if row is None:
            raise ProviderFailure("validation_error")
        profile = ProfileService().load(row)
        if (profile.provider, profile.family) == ("gemini", "generate_content"):
            if self.private_store is None:
                raise ProviderFailure("unsupported_capability")
            return GeminiAdapter(
                profile,
                private_store=self.private_store,
                resolve_credential=resolve_credential,
            ).create(request)
        if (profile.provider, profile.family) != ("openai", "images"):
            raise ProviderFailure("unsupported_capability")
        return OpenAIImagesAdapter(
            profile, resolve_credential=resolve_credential, read_timeout=self.timeout,
        ).create(request)
