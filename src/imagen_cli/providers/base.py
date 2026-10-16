"""Immutable values shared by provider adapters; no credentials or transport logic."""

from collections.abc import Mapping
from dataclasses import FrozenInstanceError, dataclass, field
from types import MappingProxyType
from typing import Any


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    if isinstance(value, bytearray):
        return bytes(value)
    return value


@dataclass(frozen=True)
class NativeContextRef:
    identity: str
    sha256: str


@dataclass(frozen=True)
class ValidatedRequest:
    plan_id: str
    job_id: str
    profile_identity: str
    profile_revision: int
    operation: str
    model: str
    model_revision: str
    prompt: str = field(repr=False)
    normalized_parameters: Mapping[str, Any]
    capability_digest: str
    authorized: bool
    dispatch_intent_recorded: bool
    inputs: tuple[bytes, ...] = field(default=(), repr=False)
    mask: bytes | None = field(default=None, repr=False)
    native_context: Any | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "normalized_parameters", _freeze(self.normalized_parameters))
        object.__setattr__(self, "inputs", tuple(bytes(item) for item in self.inputs))
        if self.mask is not None:
            object.__setattr__(self, "mask", bytes(self.mask))


@dataclass(frozen=True)
class ArtifactDescriptor:
    id: str
    index: int
    content: bytes = field(repr=False)
    media_type: str = "image/png"

    def __post_init__(self) -> None:
        object.__setattr__(self, "content", bytes(self.content))


@dataclass(frozen=True)
class ImmediateResult:
    requested_count: int
    actual_count: int
    artifacts: tuple[ArtifactDescriptor, ...]
    usage: Mapping[str, Any] = field(default_factory=dict)
    private_response: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifacts", tuple(self.artifacts))
        object.__setattr__(self, "usage", _freeze(self.usage))
        object.__setattr__(self, "private_response", _freeze(self.private_response))


@dataclass(frozen=True)
class RemoteHandle:
    """A committed remote identity; polling never creates another request."""

    remote_id: str
    polling_url: str | None = None
    cancel_url: str | None = None
    private: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "private", _freeze(self.private))


@dataclass(frozen=True)
class RemoteObservation:
    state: str
    artifacts: tuple[ArtifactDescriptor, ...] = ()
    usage: Mapping[str, Any] = field(default_factory=dict)
    private_response: Mapping[str, Any] = field(default_factory=dict, repr=False)
    detail: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifacts", tuple(self.artifacts))
        object.__setattr__(self, "usage", _freeze(self.usage))
        object.__setattr__(self, "private_response", _freeze(self.private_response))


@dataclass(frozen=True)
class CancelObservation:
    """A cancel request is not a confirmed cancel; the state says which one."""

    state: str
    detail: str | None = None


@dataclass(init=False)
class ProviderFailure(Exception):
    type: str
    message: str
    accepted_maybe: bool
    retryable: bool = field(default=False, init=False, repr=False)

    def __init__(
        self, type: str, message: str | None = None, *, accepted_maybe: bool = False,
    ) -> None:
        object.__setattr__(self, "type", type)
        object.__setattr__(self, "message", type if message is None else message)
        object.__setattr__(self, "accepted_maybe", accepted_maybe)
        object.__setattr__(self, "retryable", False)
        Exception.__init__(self, self.message)

    def __setattr__(self, name: str, value: Any) -> None:
        # Exception machinery must be able to attach tracebacks and chaining metadata.
        if name not in {
            "__traceback__", "__context__", "__cause__", "__suppress_context__", "__notes__",
        }:
            raise FrozenInstanceError(f"cannot assign to field {name!r}")
        super().__setattr__(name, value)

    def __delattr__(self, name: str) -> None:
        raise FrozenInstanceError(f"cannot delete field {name!r}")

    def __repr__(self) -> str:
        return (
            f"ProviderFailure(type={self.type!r}, message={self.message!r}, "
            f"accepted_maybe={self.accepted_maybe!r})"
        )
