from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from imagen_cli.domain.models import ProviderProfile


class ProfileService:
    """Build profiles without resolving credentials or consulting environment state."""

    _FORBIDDEN = {"api_key", "access_token", "secret", "password", "token"}

    _ENV_REFERENCE = re.compile(r"env:[A-Za-z_][A-Za-z0-9_]*\Z")

    def build(
        self,
        *,
        identity: str,
        provider: str,
        family: str,
        base_url: str,
        model_default: str,
        credential_ref: str,
        revision: int = 1,
        name: str | None = None,
    ) -> ProviderProfile:
        """Non-interactive profile construction; only env references are accepted."""
        if self._ENV_REFERENCE.fullmatch(credential_ref) is None:
            raise ValueError("credential_ref must be env:NAME")
        return ProviderProfile.model_validate({
            "id": identity,
            "name": name or identity,
            "provider": provider,
            "family": family,
            "base_url": base_url,
            "model_default": model_default,
            "credential_ref": credential_ref,
            "trust_policy": {"allowed_upload_targets": [base_url]},
            "revision": revision,
        })

    def load(self, data: Mapping[str, Any]) -> ProviderProfile:
        keys = {str(key).casefold().replace("-", "_") for key in data}
        if keys & self._FORBIDDEN:
            raise ValueError("profile accepts credential_ref, not credential values")
        if "proxy" in keys or "http_proxy" in keys or "https_proxy" in keys:
            raise ValueError("proxy configuration is not accepted; endpoint must be explicit")
        return ProviderProfile.model_validate(dict(data))
