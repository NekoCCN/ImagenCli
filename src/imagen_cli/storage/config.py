"""Explicit profile configuration: TOML file only, credential references only."""

import tomllib
from typing import Any

from imagen_cli.application.profile_service import ProfileService

__all__ = ["load_profile"]

_CREDENTIAL_SCHEMES = {"env", "keyring"}


def load_profile(path: str, identity: str) -> dict[str, Any]:
    """No ambient discovery: no .env scanning, no environment overrides."""
    with open(path, "rb") as stream:
        data = tomllib.load(stream)
    profile = ProfileService().load(data)
    if profile.id != identity:
        raise ValueError("profile identity mismatch")
    scheme, _, name = profile.credential_ref.partition(":")
    if scheme not in _CREDENTIAL_SCHEMES or not name:
        raise ValueError("credential_ref must be env:NAME or keyring:NAME")
    return profile.model_dump(mode="json")
