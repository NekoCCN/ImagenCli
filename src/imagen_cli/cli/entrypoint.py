"""Standalone entrypoint. Never display raw parser/provider exceptions or locals."""

import sys
from typing import Any, TextIO

from imagen_cli.application.container import Container

from .options import OPTION_METADATA, SharedOptions
from .registry import COMMANDS, select_output
from .renderers import Renderer, result


def _parse(argv: list[str]) -> tuple[str, list[str], SharedOptions]:
    controls: dict[str, Any] = {}
    positional: list[str] = []
    agent = False
    help_flag = False
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in {"--help", "-h"}:
            help_flag = True
        elif token == "--agent":
            agent = True
        elif token.startswith("--"):
            name, separator, raw_value = token[2:].partition("=")
            value: Any = raw_value
            if name not in OPTION_METADATA:
                raise ValueError("unknown option")
            field = name.replace("-", "_") if name != "async" else "async_"
            if field in controls:
                raise ValueError("duplicate option")
            if OPTION_METADATA[name].get("type") == "boolean":
                if separator:
                    raise ValueError("flag takes no value")
                value = True
            else:
                if not separator:
                    index += 1
                    if index == len(argv):
                        raise ValueError("option needs value")
                    value = argv[index]
                if name in {"limit", "timeout"}:
                    value = int(value)
            controls[field] = value
        else:
            positional.append(token)
        index += 1
    options = SharedOptions(**controls)
    if help_flag or not positional:
        command, args = "help", positional
    elif positional[0] in {"help", "schema"}:
        command, args = positional[0], [" ".join(positional[1:])] if positional[1:] else []
    else:
        command, args = " ".join(positional), []
    if command not in COMMANDS:
        raise ValueError("unknown command")
    if agent:
        args.append("--agent")
    return command, args, options


def main(
    argv: list[str] | None = None, *, stdout: TextIO | None = None, stderr: TextIO | None = None
) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    renderer = Renderer(select_output(argv, is_tty=stdout.isatty()), stdout, stderr)
    try:
        command, args, options = _parse(argv)
        if renderer.mode == "jsonl":
            renderer.start(command)
        status, data = Container(workspace=options.workspace).execute(command, args)
        value = result(status, data)
    except (ValueError, KeyError):
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
