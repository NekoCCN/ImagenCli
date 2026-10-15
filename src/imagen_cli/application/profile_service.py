from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from imagen_cli.domain.models import ProviderProfile


class ProfileService:
    """Build profiles without resolving credentials or consulting environment state."""

    _FORBIDDEN = {"api_key", "access_token", "secret", "password", "token"}

    def load(self, data: Mapping[str, Any]) -> ProviderProfile:
        keys = {str(key).casefold().replace("-", "_") for key in data}
        if keys & self._FORBIDDEN:
            raise ValueError("profile accepts credential_ref, not credential values")
        if "proxy" in keys or "http_proxy" in keys or "https_proxy" in keys:
            raise ValueError("proxy configuration is not accepted; endpoint must be explicit")
        return ProviderProfile.model_validate(dict(data))
