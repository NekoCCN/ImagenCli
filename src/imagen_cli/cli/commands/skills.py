"""Agent skill discovery and installation."""

from typing import Any

from imagen_cli.application import skills_service


def list_(
    agents: list[str] | None = None, *, scope: str | None = None
) -> tuple[str, dict[str, Any]]:
    return skills_service.list_(agents=agents, scope=scope)


def install_(
    names: list[str] | None,
    *,
    scope: str = "project",
    force: bool = False,
    agents: list[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    return skills_service.install_(names, scope=scope, force=force, agents=agents)


def update_(
    names: list[str] | None,
    *,
    scope: str = "project",
    force: bool = False,
    agents: list[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    return skills_service.update_(names, scope=scope, force=force, agents=agents)


def uninstall_(
    names: list[str] | None,
    *,
    scope: str = "project",
    force: bool = False,
    yes: bool = False,
    agents: list[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    return skills_service.uninstall_(names, scope=scope, force=force, yes=yes, agents=agents)
