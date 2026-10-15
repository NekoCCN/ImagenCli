"""Strict creation semantics from one explicit CLI or structured source."""

from typing import Any

from imagen_cli.cli.input_readers import read_json, read_prompt
from imagen_cli.domain.contracts import CreationInput


def parse_creation(operation: str, parameters: dict[str, Any]) -> CreationInput:
    if operation not in {"generate", "edit"}:
        raise ValueError("invalid creation operation")
    if "input_json" in parameters:
        if set(parameters) != {"input_json"}:
            raise ValueError("structured input cannot be combined with semantic options")
        data = read_json(parameters["input_json"])
    else:
        data = dict(parameters)
        allowed = {
            "session", "profile", "model", "inputs", "mask", "references", "count", "size",
            "prompt_file", "prompt_stdin", "provider_params_file",
        }
        if set(data) - allowed:
            raise ValueError("unknown creation option")
        prompt_file = data.pop("prompt_file", None)
        prompt_stdin = data.pop("prompt_stdin", False)
        if (prompt_file is not None) + bool(prompt_stdin) != 1:
            raise ValueError("exactly one prompt source is required")
        data["prompt"] = read_prompt(prompt_file=prompt_file, prompt_stdin=prompt_stdin)
        if "provider_params_file" in data:
            data["provider_params"] = read_json(data.pop("provider_params_file"))
        data.update(schema_version=1, operation=operation)
    creation = CreationInput.model_validate(data)
    if creation.operation != operation:
        raise ValueError("operation does not match command")
    return creation
