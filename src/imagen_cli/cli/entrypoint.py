"""Standalone entrypoint. Never display raw parser/provider exceptions or locals."""

import sys
from typing import Any, TextIO

from jsonschema import ValidationError, validate

from imagen_cli.application.container import Container

from .options import OPTION_METADATA, SharedOptions
from .registry import COMMANDS, discover, select_output
from .renderers import Renderer, result


def _utf8_stdio() -> None:
    """Machine output stays UTF-8 even when the Windows locale is not."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            continue


def _parse(argv: list[str]) -> tuple[str, list[str] | dict[str, Any], SharedOptions]:
    controls: dict[str, Any] = {}
    parameters: dict[str, Any] = {}
    positional: list[str] = []
    agent = False
    help_flag = False
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in {"--help", "-h"}:
            help_flag = True
        elif token == "--agent":
            if agent:
                raise ValueError("duplicate option")
            agent = True
        elif token.startswith("--"):
            name, separator, raw_value = token[2:].partition("=")
            local_option = name in {
                "intent-file", "profile", "model", "operation", "config-file", "session",
                "prompt-file", "prompt-stdin", "input-json", "input", "mask", "reference",
                "count", "size", "provider-params-file", "digest", "scope", "concurrency",
                "to", "from-turn", "artifact", "name", "plan",
                "provider", "family", "base-url", "model-default", "credential-ref",
                "revision", "pipeline", "op", "preset", "verbose", "purge",
                "send-credential", "id", "first", "second",
                "artifacts", "columns", "cell", "backend", "tile", "overlap", "accept",
                "key-file", "folder", "skill", "force",
            }
            if name not in OPTION_METADATA and not local_option:
                raise ValueError("unknown option")
            field = {"input": "inputs", "reference": "references"}.get(
                name, name.replace("-", "_") if name != "async" else "async_",
            )
            target = parameters if local_option else controls
            repeated = name in {"input", "reference", "skill"}
            if field in target and not repeated:
                raise ValueError("duplicate option")
            value: Any = raw_value
            boolean = OPTION_METADATA.get(name, {}).get("type") == "boolean" or name in {
                "send-credential", "accept", "folder", "force", "verbose", "purge",
            }
            if name == "prompt-stdin" or boolean:
                if separator:
                    raise ValueError("flag takes no value")
                value = True
            else:
                if not separator:
                    index += 1
                    if index == len(argv) or (
                        argv[index].startswith("-")
                        and not (name == "input-json" and argv[index] == "-")
                    ):
                        raise ValueError("option needs value")
                    value = argv[index]
                if name in {
                    "limit", "timeout", "count", "concurrency", "revision", "tile", "overlap",
                    "columns", "cell",
                }:
                    value = int(value)
                elif not value or "\0" in value:
                    raise ValueError("option needs text")
            if repeated:
                target.setdefault(field, []).append(value)
            else:
                target[field] = value
        elif token.startswith("-"):
            raise ValueError("unknown option")
        else:
            positional.append(token)
        index += 1
    options = SharedOptions(**controls)
    if not positional:
        command, words = "help", []
    elif positional[0] in {"help", "schema"}:
        command, words = positional[0], positional[1:]
    else:
        command = next(
            (name for name in sorted(COMMANDS, key=len, reverse=True)
             if positional[:len(name.split())] == name.split()),
            "",
        )
        if not command:
            raise ValueError("unknown command")
        words = positional[len(command.split()):]
        if help_flag:
            command, words = "help", command.split()
    if help_flag:
        command = "help"
    common = {"workspace", "output", "no_interactive"}
    if command in {"help", "schema"}:
        target_command = " ".join(words) if words else None
        if parameters or set(controls) - common:
            raise ValueError("unexpected option")
        if target_command is not None and target_command not in COMMANDS:
            raise ValueError("unknown help target")
        args = ([target_command] if target_command else []) + (["--agent"] if agent else [])
        return command, args, options
    extra_controls = {
        "providers": {"limit", "cursor"},
        "models": {"limit", "cursor"},
        "profile list": {"limit", "cursor"},
        "session list": {"limit", "cursor"},
        "job wait": {"timeout"},
        "batch wait": {"timeout"},
        "plan execute": {"timeout"},
        "session delete": {"yes"},
        "session remove-artifact": {"purge"},
        "session remove-image": {"purge"},
        "asset gc": {"dry_run", "yes"},
        "cache purge": {"plan", "yes"},
        "transform": {"pipeline", "op", "preset", "session"},
        "ops": {"op", "preset", "verbose"},
        "models refresh": {"profile", "send_credential"},
        "models add": {"profile", "id"},
        "models list": {"profile"},
        "profile set": {
            "provider", "family", "base_url", "model_default", "credential_ref", "revision",
        },
        "generate": {"dry_run", "timeout", "wait", "async_"},
        "edit": {"dry_run", "timeout", "wait", "async_"},
    }.get(command, set())
    if set(controls) - common - extra_controls:
        raise ValueError("unexpected option")
    schema = discover(command)["input_schema"]
    if command.startswith("profile "):
        if command == "profile list":
            if words or parameters:
                raise ValueError("unexpected arguments")
        else:
            if len(words) != 1:
                raise ValueError("unexpected positional arguments")
            parameters["profile"] = words[0]
        validate(parameters, schema)
        return command, parameters, options
    if command in {"providers", "models"}:
        if words:
            raise ValueError("unexpected positional arguments")
        validate(parameters, schema)
        return command, parameters, options
    if command in {"plan show", "plan approve", "plan execute", "plan retry"}:
        if len(words) != 1:
            raise ValueError("one plan identity required")
        parameters["plan_id"] = words[0]
        validate(parameters, schema)
        return command, parameters, options
    if command in {"worker start", "worker status", "worker recover"}:
        if words or parameters:
            raise ValueError("unexpected arguments")
        return command, {}, options
    if command in {"generate", "edit"}:
        if words:
            raise ValueError("unexpected positional arguments")
        if not parameters:
            return command, [], options
        from imagen_cli.cli.creation_input import parse_creation

        creation = parse_creation(command, parameters)
        return command, creation.model_dump(mode="json", exclude_none=True), options
    if command == "batch submit":
        if words or set(parameters) - {"input_json", "concurrency"}:
            raise ValueError("unexpected arguments")
        if "input_json" not in parameters:
            raise ValueError("structured input is required")
        from imagen_cli.cli.input_readers import read_json

        payload = read_json(parameters["input_json"])
        if "concurrency" in parameters:
            if "concurrency" in payload:
                raise ValueError("duplicate option")
            payload["concurrency"] = parameters["concurrency"]
        validate(payload, schema)
        return command, payload, options
    if command in {"session fork", "session export", "session delete"}:
        if len(words) != 1:
            raise ValueError("one session identity required")
        parameters["session"] = words[0]
        if command == "session delete" and options.yes:
            parameters["yes"] = True
        validate(parameters, schema)
        return command, parameters, options
    if command in {"session remove-artifact", "session remove-image"}:
        if len(words) != 2:
            raise ValueError("session and artifact identities required")
        parameters["session"] = words[0]
        parameters["artifact"] = words[1]
        validate(parameters, schema)
        return command, parameters, options
    if command == "session import":
        if len(words) != 1 or parameters:
            raise ValueError("one archive path required")
        parameters["archive"] = words[0]
        validate(parameters, schema)
        return command, parameters, options
    if command == "transform":
        if len(words) != 1:
            raise ValueError("one artifact identity required")
        parameters["artifact"] = words[0]
        if "pipeline" not in parameters and "op" not in parameters and "preset" not in parameters:
            raise ValueError("pipeline, op or preset is required")
        validate(parameters, schema)
        return command, parameters, options
    if command.startswith("models "):
        # Positional profile is shorthand for --profile, e.g. `models use demo --id m`.
        if len(words) > 1:
            raise ValueError("unexpected positional arguments")
        if words:
            if "profile" in parameters:
                raise ValueError("duplicate option")
            parameters["profile"] = words[0]
        validate(parameters, schema)
        return command, parameters, options
    if command in {"skills list", "skills install"}:
        if words:
            raise ValueError("unexpected positional arguments")
        validate(parameters, schema)
        return command, parameters, options
    if command.startswith("cache "):
        if words:
            raise ValueError("unexpected positional arguments")
        if options.yes:
            parameters["yes"] = True
        validate(parameters, schema)
        return command, parameters, options
    if command == "ops":
        if len(words) > 1:
            raise ValueError("unexpected positional arguments")
        if words:
            if "op" in parameters:
                raise ValueError("duplicate option")
            parameters["op"] = words[0]
        validate(parameters, schema)
        return command, parameters, options
    if command == "superres models":
        if words or parameters:
            raise ValueError("unexpected arguments")
        return command, {}, options
    if command in {"compare", "sheet", "superres fetch", "superres benchmark"}:
        if words:
            raise ValueError("unexpected positional arguments")
        validate(parameters, discover(command)["input_schema"])
        return command, parameters, options
    if command in {"asset show", "asset delete", "asset remove"}:
        if len(words) != 1:
            raise ValueError("one artifact identity required")
        parameters["artifact"] = words[0]
        validate(parameters, schema)
        return command, parameters, options
    if command == "asset open":
        if len(words) != 1:
            raise ValueError("one artifact identity required")
        parameters["artifact_id"] = words[0]
        validate(parameters, schema)
        return command, parameters, options
    if command == "asset gc":
        if words:
            raise ValueError("unexpected positional arguments")
        if options.dry_run:
            parameters["dry_run"] = True
        if options.yes:
            parameters["yes"] = True
        validate(parameters, schema)
        return command, parameters, options
    if command.startswith("session ") and command in {
        "session create", "session list", "session show", "session resume"
    } or command in {
        "job get", "job wait", "job fetch", "job reconcile", "job cancel",
        "batch get", "batch wait",
    }:
        identity = ("name" if command == "session create" else
                    "session" if command.startswith("session ") else
                    "batch_id" if command.startswith("batch ") else "job_id")
        required_words = 0 if command == "session list" else 1
        if len(words) != required_words:
            raise ValueError("unexpected positional arguments")
        if words:
            parameters[identity] = words[0]
        validate(parameters, schema)
        return command, parameters, options

    if words or parameters:
        raise ValueError("unexpected arguments")
    return command, {}, options


def main(
    argv: list[str] | None = None, *, stdout: TextIO | None = None, stderr: TextIO | None = None
) -> int:
    if stdout is None and stderr is None:
        _utf8_stdio()
    argv = list(sys.argv[1:] if argv is None else argv)
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    renderer = Renderer(select_output(argv, is_tty=stdout.isatty()), stdout, stderr)
    try:
        command, args, options = _parse(argv)
        if renderer.mode == "jsonl":
            renderer.start(command)
        with Container(workspace=options.workspace, options=options) as container:
            status, data = container.execute(command, args)
        value = result(status, data)
    except (ValueError, KeyError, ValidationError):
        value = result("validation_error")
    except KeyboardInterrupt:
        value = result("canceled")
    except Exception:
        value = result("internal_error")
    try:
        return renderer.finish(value)
    except (BrokenPipeError, OSError):
        # Never rerun the command after an output failure.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
