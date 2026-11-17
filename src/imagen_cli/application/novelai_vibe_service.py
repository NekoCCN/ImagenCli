"""Explicit preparation approval and exact private cache reuse. Never auto-encode."""

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from imagen_cli.application.profile_service import ProfileService
from imagen_cli.domain.models import ProviderProfile
from imagen_cli.domain.primitive import new_id
from imagen_cli.providers.base import ProviderFailure, ReferenceInput
from imagen_cli.storage.artifacts import MAX_BYTES, ArtifactStore, inspect_media
from imagen_cli.storage.private_state import PrivateStateStore
from imagen_cli.storage.repositories import Repositories

CHARGE_DISCLOSURE = "Vibe preparation may incur a separate charge; amount unknown."


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


class Encoder(Protocol):
    def encode(self, request: Any) -> bytes: ...


class VibePreparationService:
    def __init__(
        self,
        repositories: Repositories,
        artifacts: ArtifactStore,
        private_store: PrivateStateStore,
        *,
        adapter_factory: Callable[[ProviderProfile], Encoder] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if artifacts.repositories.connection is not repositories.connection:
            raise ValueError("preparation dependencies must share a connection")
        self.repositories = repositories
        self.artifacts = artifacts
        self.private_store = private_store
        self.adapter_factory = adapter_factory or self._adapter
        self.clock = clock or (lambda: datetime.now(UTC))

    def _adapter(self, profile: ProviderProfile) -> Encoder:
        from imagen_cli.execution.configured import resolve_credential
        from imagen_cli.providers.novelai_vibe import NovelAIVibeAdapter

        return NovelAIVibeAdapter(
            profile,
            resolve_credential=lambda ref: resolve_credential(
                ref,
                private_store=self.private_store,
            ),
        )

    def _profile(self, profile_id: str) -> ProviderProfile:
        row = self.repositories.get("profiles", profile_id)
        if row is None:
            raise ValueError("profile unavailable")
        return ProfileService().load(row)

    def _source(self, artifact_id: str) -> bytes:
        path = self.artifacts.get(artifact_id)
        row = self.repositories.get("artifacts", artifact_id)
        if path is None or row is None:
            raise ValueError("published source artifact required")
        with path.open("rb") as source:
            content = source.read(MAX_BYTES + 1)
        measured = inspect_media(content)
        if not str(measured["media_type"]).startswith("image/"):
            raise ValueError("Vibe preparation requires an image")
        if any(row.get(key) != value for key, value in measured.items()):
            raise ValueError("source artifact changed")
        return content

    def plan(
        self, profile_id: str, artifact_id: str, model: str, information_extracted: float
    ) -> dict[str, Any]:
        from imagen_cli.providers.novelai_vibe import identity

        content = self._source(artifact_id)
        profile = self._profile(profile_id)
        key = identity(
            profile,
            hashlib.sha256(content).hexdigest(),
            model,
            information_extracted,
        )
        now = self.clock()
        plan = {
            "identity": key,
            "artifact_id": artifact_id,
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=24)).isoformat(),
            "charge_disclosure": CHARGE_DISCLOSURE,
            "operation": "encode_vibe",
        }
        reuse = self.lookup(profile, key["source_sha256"], model, information_extracted)
        if reuse is not None:
            plan["reuse"] = reuse
        unknown = self.repositories.unresolved_vibe_preparations(_digest(key))
        if unknown and reuse is None:
            plan["duplicate_cost_warning"] = (
                "A prior preparation has an unknown outcome and may have charged. "
                "Executing this new plan authorizes another possibly charged request."
            )
            plan["prior_unknown_preparation_ids"] = unknown
            plan["prior_unknown_preparation_id"] = unknown[-1]
        plan_id = new_id("vib")
        self.repositories.insert(
            "vibe_preparations",
            {
                "id": plan_id,
                "cache_key": _digest(key),
                "state": "planned",
                "consumed": 0,
                "plan": plan,
                "digest": _digest(plan),
                "created_at": plan["created_at"],
                "expires_at": plan["expires_at"],
                "updated_at": plan["created_at"],
                "accepted_maybe": False,
            },
        )
        return self.status(plan_id)

    def _validated(self, plan_id: str, digest: str) -> dict[str, Any]:
        row = self.repositories.get("vibe_preparations", plan_id, include_private=True)
        if row is None or row["digest"] != digest or _digest(row["plan"]) != digest:
            raise ValueError("preparation digest mismatch")
        if row["cache_key"] != _digest(row["plan"]["identity"]):
            raise ValueError("preparation cache identity mismatch")
        return dict(row)

    def status(self, plan_id: str) -> dict[str, Any]:
        row = self.repositories.get("vibe_preparations", plan_id)
        if row is None:
            raise KeyError(plan_id)
        self._validated(plan_id, row["digest"])
        return {**row, "charge_disclosure": CHARGE_DISCLOSURE}

    def execute(self, plan_id: str, digest: str) -> dict[str, Any]:
        from imagen_cli.providers.novelai_vibe import (
            MAX_ENCODING_BYTES,
            VibeEncodingRequest,
            identity,
        )

        if self.repositories.connection.in_transaction:
            raise ValueError("preparation dispatch requires independent transaction")
        # BEGIN IMMEDIATE serializes competing consumers across processes. Unknown
        # is committed BEFORE constructing transport/resolving a credential.
        with self.repositories.transaction():
            row = self._validated(plan_id, digest)
            if row["consumed"]:
                return self.status(plan_id)
            plan = row["plan"]
            if datetime.fromisoformat(plan["expires_at"]) <= self.clock():
                raise ValueError("preparation plan expired")
            profile = self._profile(plan["identity"]["profile_identity"])
            content = self._source(plan["artifact_id"])
            expected = identity(
                profile,
                hashlib.sha256(content).hexdigest(),
                plan["identity"]["model"],
                plan["identity"]["information_extracted"],
            )
            if expected != plan["identity"]:
                raise ValueError("preparation identity drifted; create a new vibe plan")
            # Reconcile *all* same-identity attempts while holding BEGIN IMMEDIATE.
            # An independent approval may have been planned before another attempt
            # committed its intent/result. Reuse new success without another charge;
            # new unresolved risk requires newly disclosed digest approval.
            evidence = self.lookup(
                profile,
                expected["source_sha256"],
                expected["model"],
                expected["information_extracted"],
            )
            cached: bytes | None = None
            if evidence is not None:
                prior = self.repositories.get(
                    "vibe_preparations",
                    evidence["preparation_id"],
                    include_private=True,
                )
                cached = self._read(prior)
            else:
                unknown = self.repositories.unresolved_vibe_preparations(row["cache_key"])
                disclosed = plan.get("prior_unknown_preparation_ids", [])
                if (
                    unknown != disclosed
                    or (unknown and not plan.get("duplicate_cost_warning"))
                    or "reuse" in plan
                ):
                    raise ValueError("duplicate preparation risk changed; replan for approval")
            self.repositories.update(
                "vibe_preparations",
                plan_id,
                {
                    "consumed": 1,
                    "state": "unknown",
                    "accepted_maybe": cached is None,
                    "updated_at": self.clock().isoformat(),
                },
            )
        try:
            if cached is not None:
                encoded = cached
            else:
                encoded = self.adapter_factory(profile).encode(
                    VibeEncodingRequest(
                        expected,
                        content,
                        authorized=True,
                        dispatch_intent_recorded=True,
                    )
                )
            if not isinstance(encoded, bytes) or not 0 < len(encoded) <= MAX_ENCODING_BYTES:
                raise ProviderFailure("provider_error", accepted_maybe=True)
            locator = self.private_store.write(plan_id, encoded)
            self.repositories.update(
                "vibe_preparations",
                plan_id,
                {
                    "state": "succeeded",
                    **(
                        {"reused_preparation_id": evidence["preparation_id"]}
                        if evidence is not None
                        else {}
                    ),
                    "encoding_sha256": locator["sha256"],
                    "private_relpath": locator["private_relpath"],
                    "byte_size": len(encoded),
                    "accepted_maybe": False,
                    "updated_at": self.clock().isoformat(),
                },
            )
        except ProviderFailure as error:
            self.repositories.update(
                "vibe_preparations",
                plan_id,
                {
                    "state": "unknown" if error.accepted_maybe else "failed",
                    "accepted_maybe": error.accepted_maybe,
                    "error_type": error.type,
                    "updated_at": self.clock().isoformat(),
                },
            )
        except (Exception, KeyboardInterrupt):
            # Keep the conservative durable state. Never log service bodies/bytes.
            pass
        return self.status(plan_id)

    def lookup(
        self, profile: ProviderProfile, source_sha256: str, model: str, information_extracted: float
    ) -> dict[str, Any] | None:
        from imagen_cli.providers.novelai_vibe import identity

        key = identity(profile, source_sha256, model, information_extracted)
        row = self.repositories.latest_vibe_preparation(_digest(key))
        if row is None:
            return None
        self._validated(row["id"], row["digest"])
        if row["plan"]["identity"] != key or not row["consumed"]:
            raise ValueError("preparation provenance mismatch")
        self._read(row)
        return {
            "preparation_id": row["id"],
            "preparation_digest": row["digest"],
            "cache_key": row["cache_key"],
            "encoding_sha256": row["encoding_sha256"],
            "byte_size": row["byte_size"],
            "identity": key,
        }

    def _read(self, row: Mapping[str, Any]) -> bytes:
        from imagen_cli.providers.novelai_vibe import MAX_ENCODING_BYTES

        if row.get("private_relpath") != row["id"] + ".bin":
            raise ValueError("preparation private locator mismatch")
        content = self.private_store.read(
            row["id"], row["encoding_sha256"], max_bytes=MAX_ENCODING_BYTES
        )
        if not 0 < len(content) <= MAX_ENCODING_BYTES or len(content) != row["byte_size"]:
            raise ValueError("preparation encoding size mismatch")
        return content

    def bind(
        self,
        profile: ProviderProfile,
        reference: ReferenceInput,
        model: str,
        *,
        expected: Mapping[str, Any] | None = None,
    ) -> ReferenceInput:
        if reference.kind != "vibe" or (
            profile.novelai_options and profile.novelai_options.reference_transport == "raw"
        ):
            return reference
        evidence = self.lookup(
            profile,
            hashlib.sha256(reference.content).hexdigest(),
            model,
            reference.settings.get("information_extracted", 1),
        )
        if evidence is None:
            raise ValueError(
                "encoded Vibe cache missing; run imagen vibe plan, "
                "then vibe execute with its digest"
            )
        if expected is not None and dict(expected) != evidence:
            raise ValueError("approved Vibe preparation drifted")
        row = self.repositories.get(
            "vibe_preparations", evidence["preparation_id"], include_private=True
        )
        return replace(reference, prepared_content=self._read(row), encoding=evidence)
