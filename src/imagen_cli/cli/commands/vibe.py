"""Explicit two-stage Vibe preparation; status never dispatches."""

from typing import Any

from imagen_cli.application.novelai_vibe_service import VibePreparationService


def execute(
    service: VibePreparationService, command: str, args: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    if command == "vibe plan":
        row = service.plan(
            args["profile"],
            args["artifact_id"],
            args["model"],
            args.get("information_extracted", 1),
        )
        return "planned", {"preparation": row}
    if command == "vibe execute":
        row = service.execute(args["preparation_id"], args["digest"])
        return ("unknown" if row["state"] == "planned" else row["state"]), {"preparation": row}
    if command == "vibe status":
        return "succeeded", {"preparation": service.status(args["preparation_id"])}
    raise ValueError("unsupported Vibe command")
