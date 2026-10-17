"""Invocation controls, independent of creation semantics."""

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SharedOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    workspace: str | None = None
    output: Literal["human", "json", "jsonl"] | None = None
    no_interactive: bool = False
    dry_run: bool = False
    timeout: int = Field(default=300, ge=1, le=86400)
    wait: bool = False
    async_: bool = False
    yes: bool = False
    limit: int = Field(default=100, ge=1, le=1000)
    cursor: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,2048}$")

    @model_validator(mode="after")
    def exclusive_wait(self) -> Self:
        if self.wait and self.async_:
            raise ValueError("wait and async are mutually exclusive")
        return self


OPTION_METADATA: dict[str, dict[str, Any]] = {
    "workspace": {"type": "string", "default": None},
    "output": {"enum": ["human", "json", "jsonl"], "default": "human"},
    "agent": {"type": "boolean", "default": False},
    "no-interactive": {"type": "boolean", "default": False},
    "dry-run": {"type": "boolean", "default": False},
    "timeout": {"type": "integer", "default": 300, "minimum": 1, "maximum": 86400},
    "wait": {"type": "boolean", "default": False},
    "async": {"type": "boolean", "default": False},
    "yes": {"type": "boolean", "default": False},
    "limit": {"type": "integer", "default": 100, "minimum": 1, "maximum": 1000},
    "cursor": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,2048}$"},
}
