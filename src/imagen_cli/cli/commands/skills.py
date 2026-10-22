"""Agent skill discovery and installation."""

from typing import Any

from imagen_cli.application import skills_service


def list_() -> tuple[str, dict[str, Any]]:
    return skills_service.list_()


def install_(
    names: list[str] | None, *, scope: str = "project", force: bool = False,
) -> tuple[str, dict[str, Any]]:
    return skills_service.install_(names, scope=scope, force=force)
