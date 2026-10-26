"""Standalone entrypoint. Never display raw parser/provider exceptions or locals."""

import difflib
import sys
from typing import Any, TextIO

from jsonschema import ValidationError, validate

from imagen_cli import i18n
from imagen_cli.application.container import Container

from .options import COMMAND_CONTROLS, OPTION_METADATA, SharedOptions
from .registry import COMMANDS, discover, select_output
from .renderers import Renderer, result
from .usage import UsageError, describe_pydantic, describe_validation, plain_detail


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


def _no_positional(command: str, words: list[str]) -> UsageError:
    found = " ".join(words)
    return UsageError(
        i18n.t(
            f"{command} takes no positional arguments; got '{found}'.",
            f"{command} 不接受位置参数，实际收到 '{found}'。",
        ),
        f"imagen help {command}",
    )


def _no_arguments(command: str, words: list[str], parameters: Any) -> UsageError:
    if words:
        return _no_positional(command, words)
    names = ", ".join("--" + str(name).replace("_", "-") for name in sorted(parameters))
    return UsageError(
        i18n.t(
            f"{command} takes no options; got {names or 'extra arguments'}.",
            f"{command} 不接受选项，实际收到 {names or '多余参数'}。",
        ),
        f"imagen help {command}",
    )


def _need(command: str, words: list[str], count: int, en: str, zh: str) -> UsageError:
    return UsageError(
        i18n.t(
            f"{command} needs {en}; got {len(words)}.",
            f"{command} 需要{zh}；实际收到 {len(words)} 个。",
        ),
        f"imagen help {command}",
    )


def _command_hint(argv: list[str]) -> str:
    """The help command that lists what the caller was reaching for."""
    for index, token in enumerate(argv):
        if token.startswith("-"):
            continue
        for name in sorted(COMMANDS, key=len, reverse=True):
            words = name.split()
            if argv[index : index + len(words)] == words:
                return f"imagen help {name}"
    return "imagen help"


def installed_version() -> str:
    """The installed distribution's version; "unknown" when run from a source tree."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("imagen-cli")
    except PackageNotFoundError:
        return "unknown"


def _parse(argv: list[str]) -> tuple[str, list[str] | dict[str, Any], SharedOptions]:
    controls: dict[str, Any] = {}
    parameters: dict[str, Any] = {}
    positional: list[str] = []
    agent = False
    language_seen = False
    help_flag = False
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in {"--version", "-V"}:
            return "version", {}, SharedOptions()
        if token in {"--help", "-h"}:
            help_flag = True
        elif token == "--agent":
            if agent:
                raise UsageError(
                    i18n.t("Option --agent appears twice.", "选项 --agent 重复出现。")
                )
            agent = True
        elif token in {"--zh", "--en"}:
            if language_seen:
                raise UsageError(
                    i18n.t(
                        "Choose one language option only (--zh, --en or --lang).",
                        "只能指定一个语言选项（--zh、--en 或 --lang）。",
                    )
                )
            language_seen = True
        elif token == "--lang" or token.startswith("--lang="):
            # main() applies the language before parsing and removes the option,
            # so this branch only validates direct parser callers.
            if language_seen:
                raise UsageError(
                    i18n.t(
                        "Choose one language option only (--zh, --en or --lang).",
                        "只能指定一个语言选项（--zh、--en 或 --lang）。",
                    )
                )
            language_seen = True
            if token == "--lang":
                index += 1
                if index == len(argv) or argv[index].startswith("-"):
                    raise UsageError(
                        i18n.t(
                            "Option --lang needs a value: en or zh-CN.",
                            "选项 --lang 需要取值：en 或 zh-CN。",
                        )
                    )
                language_value = argv[index]
            else:
                language_value = token.partition("=")[2]
            if not language_value or i18n.normalize(language_value) is None:
                raise UsageError(
                    i18n.t(
                        f"Unsupported language '{language_value}'. Use en or zh-CN.",
                        f"不支持的语言 '{language_value}'。请使用 en 或 zh-CN。",
                    )
                )
        elif token.startswith("--"):
            name, separator, raw_value = token[2:].partition("=")
            local_option = name in {
                "intent-file",
                "intent",
                "install",
                "profile",
                "model",
                "operation",
                "config-file",
                "session",
                "prompt-file",
                "prompt-stdin",
                "input-json",
                "input",
                "mask",
                "reference",
                "count",
                "size",
                "provider-params-file",
                "digest",
                "scope",
                "concurrency",
                "to",
                "from-turn",
                "artifact",
                "name",
                "plan",
                "provider",
                "family",
                "base-url",
                "model-default",
                "credential-ref",
                "revision",
                "pipeline",
                "op",
                "preset",
                "verbose",
                "purge",
                "send-credential",
                "id",
                "first",
                "second",
                "artifacts",
                "columns",
                "cell",
                "backend",
                "tile",
                "overlap",
                "accept",
                "key-file",
                "folder",
                "skill",
                "force",
                "target",
                "global",
            }
            if name not in OPTION_METADATA and not local_option:
                raise UsageError(i18n.t(f"Unknown option --{name}.", f"未知选项 --{name}。"))
            field = {"input": "inputs", "reference": "references"}.get(
                name,
                name.replace("-", "_") if name != "async" else "async_",
            )
            target = parameters if local_option else controls
            repeated = name in {"input", "reference", "skill", "target"}
            if field in target and not repeated:
                raise UsageError(
                    i18n.t(f"Option --{name} appears twice.", f"选项 --{name} 重复出现。")
                )
            value: Any = raw_value
            boolean = OPTION_METADATA.get(name, {}).get("type") == "boolean" or name in {
                "send-credential",
                "accept",
                "folder",
                "force",
                "verbose",
                "purge",
                "global",
                "install",
            }
            if name == "prompt-stdin" or boolean:
                if separator:
                    raise UsageError(
                        i18n.t(
                            f"--{name} is a flag and takes no value; drop '={raw_value}'.",
                            f"--{name} 是开关，不接受取值；请去掉 '={raw_value}'。",
                        )
                    )
                value = True
            else:
                if not separator:
                    index += 1
                    if index == len(argv) or (
                        argv[index].startswith("-")
                        and not (name == "input-json" and argv[index] == "-")
                    ):
                        raise UsageError(
                            i18n.t(
                                f"Option --{name} needs a value, e.g. --{name}=VALUE.",
                                f"选项 --{name} 需要一个取值，例如 --{name}=值。",
                            )
                        )
                    value = argv[index]
                if name in {
                    "limit",
                    "timeout",
                    "count",
                    "concurrency",
                    "revision",
                    "tile",
                    "overlap",
                    "columns",
                    "cell",
                }:
                    try:
                        value = int(value)
                    except ValueError:
                        raise UsageError(
                            i18n.t(
                                f"--{name} must be a whole number, got '{value}'.",
                                f"--{name} 必须是整数，当前为 '{value}'。",
                            )
                        ) from None
                elif not value or "\0" in value:
                    raise UsageError(
                        i18n.t(
                            f"--{name} needs non-empty text.",
                            f"--{name} 需要非空文本。",
                        )
                    )
            if repeated:
                target.setdefault(field, []).append(value)
            else:
                target[field] = value
        elif token.startswith("-"):
            raise UsageError(i18n.t(f"Unknown option {token}.", f"未知选项 {token}。"))
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
            (
                name
                for name in sorted(COMMANDS, key=len, reverse=True)
                if positional[: len(name.split())] == name.split()
            ),
            "",
        )
        if not command:
            guess = difflib.get_close_matches(
                " ".join(positional[:2]).strip(), sorted(COMMANDS), n=1, cutoff=0.6
            ) or difflib.get_close_matches(positional[0], sorted(COMMANDS), n=1, cutoff=0.6)
            if guess:
                raise UsageError(
                    i18n.t(
                        f"Unknown command '{positional[0]}'. Did you mean '{guess[0]}'?",
                        f"未知命令 '{positional[0]}'。是不是 '{guess[0]}'？",
                    ),
                    f"imagen help {guess[0]}",
                )
            raise UsageError(
                i18n.t(f"Unknown command '{positional[0]}'.", f"未知命令 '{positional[0]}'。"),
                "imagen help",
            )
        words = positional[len(command.split()) :]
        if help_flag:
            command, words = "help", command.split()
    if help_flag:
        command = "help"
    common = {"workspace", "output", "no_interactive"}
    if command in {"help", "schema"}:
        target_command = " ".join(words) if words else None
        if parameters or set(controls) - common:
            raise UsageError(
                i18n.t(
                    "help and schema accept only --workspace, --output and --no-interactive.",
                    "help 和 schema 只接受 --workspace、--output 与 --no-interactive。",
                ),
                "imagen help",
            )
        if target_command is not None and target_command not in COMMANDS:
            group = sorted(
                name for name in COMMANDS if name.startswith(f"{target_command} ")
            )
            if group:
                raise UsageError(
                    i18n.t(
                        f"'{target_command}' is a command group: {', '.join(group)}.",
                        f"'{target_command}' 是一组命令：{', '.join(group)}。",
                    ),
                    f"imagen help {group[0]}",
                )
            raise UsageError(
                i18n.t(
                    f"Unknown help target '{target_command}'.",
                    f"未知的帮助目标 '{target_command}'。",
                ),
                "imagen help",
            )
        args = ([target_command] if target_command else []) + (["--agent"] if agent else [])
        return command, args, options
    extra_controls = COMMAND_CONTROLS.get(command, set())
    unexpected = sorted(set(controls) - common - extra_controls)
    if unexpected:
        names = ", ".join("--" + name.replace("_", "-") for name in unexpected)
        raise UsageError(
            i18n.t(
                f"{command} does not accept {names}.",
                f"{command} 不接受 {names}。",
            ),
            f"imagen help {command}",
        )
    schema = discover(command)["input_schema"]
    if command == "update":
        if words:
            raise _no_positional(command, words)
        validate(parameters, schema)
        return command, parameters, options
    if command == "language":
        if parameters:
            raise UsageError(
                i18n.t(
                    "language takes its value as a word: imagen language en",
                    "language 的取值直接写在后面：imagen language en",
                ),
                "imagen help language",
            )
        if len(words) > 1:
            raise _need(command, words, 1, "one language", "一个语言值")
        if words:
            parameters["language"] = words[0]
        validate(parameters, schema)
        return command, parameters, options
    if command.startswith("profile "):
        if command == "profile list":
            if words or parameters:
                raise _no_arguments(command, words, parameters)
        else:
            if len(words) != 1:
                raise _need(command, words, 1, "one profile id", "一个 profile 标识")
            parameters["profile"] = words[0]
        validate(parameters, schema)
        return command, parameters, options
    if command in {"providers", "models"}:
        if words:
            raise _no_positional(command, words)
        validate(parameters, schema)
        return command, parameters, options
    if command in {"plan show", "plan approve", "plan execute", "plan retry"}:
        if len(words) != 1:
            raise _need(command, words, 1, "one plan id", "一个 plan 标识")
        parameters["plan_id"] = words[0]
        validate(parameters, schema)
        return command, parameters, options
    if command in {"worker start", "worker status", "worker recover"}:
        if words or parameters:
            raise _no_arguments(command, words, parameters)
        return command, {}, options
    if command in {"generate", "edit"}:
        if words:
            raise _no_positional(command, words)
        if not parameters:
            return command, [], options
        from imagen_cli.cli.creation_input import parse_creation

        creation = parse_creation(command, parameters)
        return command, creation.model_dump(mode="json", exclude_none=True), options
    if command == "batch submit":
        if words or set(parameters) - {"input_json", "concurrency"}:
            raise _no_arguments(command, words, set(parameters) - {"input_json", "concurrency"})
        if "input_json" not in parameters:
            raise UsageError(
                i18n.t(
                    "batch submit needs a JSON file: --input-json path.json",
                    "batch submit 需要 JSON 文件：--input-json 路径.json",
                ),
                "imagen help batch submit",
            )
        from imagen_cli.cli.input_readers import read_json

        payload = read_json(parameters["input_json"])
        if "concurrency" in parameters:
            if "concurrency" in payload:
                raise UsageError(
                    i18n.t(
                        "concurrency is set both in the JSON file and with --concurrency.",
                        "并发数同时出现在 JSON 文件和 --concurrency 中。",
                    )
                )
            payload["concurrency"] = parameters["concurrency"]
        validate(payload, schema)
        return command, payload, options
    if command in {"session fork", "session export", "session delete"}:
        if len(words) != 1:
            raise _need(command, words, 1, "one session id", "一个会话标识")
        parameters["session"] = words[0]
        if command == "session delete" and options.yes:
            parameters["yes"] = True
        validate(parameters, schema)
        return command, parameters, options
    if command in {"session remove-artifact", "session remove-image"}:
        if len(words) != 2:
            raise _need(command, words, 2, "a session id and an artifact id", "会话标识和产物标识")
        parameters["session"] = words[0]
        parameters["artifact"] = words[1]
        validate(parameters, schema)
        return command, parameters, options
    if command == "session import":
        if len(words) != 1 or parameters:
            raise _need(command, words, 1, "one archive path", "一个归档路径")
        parameters["archive"] = words[0]
        validate(parameters, schema)
        return command, parameters, options
    if command == "transform":
        if len(words) != 1:
            raise _need(command, words, 1, "one artifact id", "一个产物标识")
        parameters["artifact"] = words[0]
        if "timeout" in controls:
            parameters["timeout"] = options.timeout
        if "pipeline" not in parameters and "op" not in parameters and "preset" not in parameters:
            raise UsageError(
                i18n.t(
                    "transform needs --preset, --op or --pipeline; "
                    "run `imagen ops` to list them.",
                    "transform 需要 --preset、--op 或 --pipeline；运行 `imagen ops` 查看列表。",
                ),
                "imagen ops",
            )
        validate(parameters, schema)
        return command, parameters, options
    if command.startswith("models "):
        # Positional profile is shorthand for --profile, e.g. `models use demo --id m`.
        if len(words) > 1:
            raise _no_positional(command, words[1:])
        if words:
            if "profile" in parameters:
                raise UsageError(
                    i18n.t(
                        "Give the profile either as a word or with --profile, not both.",
                        "profile 既作为位置参数又用了 --profile，二选一。",
                    )
                )
            parameters["profile"] = words[0]
        validate(parameters, schema)
        return command, parameters, options
    if command in {"skills list", "skills install", "skills update", "skills uninstall"}:
        if words:
            raise _no_positional(command, words)
        if parameters.pop("global", False):
            if parameters.get("scope") == "project":
                raise UsageError(
                    i18n.t(
                        "--global conflicts with --scope project; pick one.",
                        "--global 与 --scope project 冲突，只能选一个。",
                    )
                )
            parameters["scope"] = "global"
        if options.yes:
            parameters["yes"] = True
        validate(parameters, schema)
        return command, parameters, options
    if command.startswith("cache "):
        if words:
            raise _no_positional(command, words)
        if options.yes:
            parameters["yes"] = True
        validate(parameters, schema)
        return command, parameters, options
    if command == "ops":
        if len(words) > 1:
            raise _no_positional(command, words[1:])
        if words:
            if "op" in parameters:
                raise UsageError(
                    i18n.t(
                        "Give the operator either as a word or with --op, not both.",
                        "算子既作为位置参数又用了 --op，二选一。",
                    )
                )
            parameters["op"] = words[0]
        validate(parameters, schema)
        return command, parameters, options
    if command == "superres models":
        if words or parameters:
            raise _no_arguments(command, words, parameters)
        return command, {}, options
    if command == "superres fetch" and words:
        if len(words) != 1 or "model" in parameters:
            raise _need(command, words, 1, "one model name", "一个模型名")
        parameters["model"] = words[0]
        words = []
    if command in {"compare", "sheet", "superres fetch", "superres benchmark"}:
        if words:
            raise _no_positional(command, words)
        validate(parameters, discover(command)["input_schema"])
        return command, parameters, options
    if command in {"asset show", "asset delete", "asset remove"}:
        if len(words) != 1:
            raise _need(command, words, 1, "one artifact id", "一个产物标识")
        parameters["artifact"] = words[0]
        validate(parameters, schema)
        return command, parameters, options
    if command == "asset open":
        if len(words) != 1:
            raise _need(command, words, 1, "one artifact id", "一个产物标识")
        parameters["artifact_id"] = words[0]
        validate(parameters, schema)
        return command, parameters, options
    if command == "asset gc":
        if words:
            raise _no_positional(command, words)
        if options.dry_run:
            parameters["dry_run"] = True
        if options.yes:
            parameters["yes"] = True
        validate(parameters, schema)
        return command, parameters, options
    if (
        command.startswith("session ")
        and command in {"session create", "session list", "session show", "session resume"}
        or command
        in {
            "job get",
            "job wait",
            "job fetch",
            "job reconcile",
            "job cancel",
            "batch get",
            "batch wait",
        }
    ):
        identity = (
            "name"
            if command == "session create"
            else "session"
            if command.startswith("session ")
            else "batch_id"
            if command.startswith("batch ")
            else "job_id"
        )
        required_words = 0 if command == "session list" else 1
        if len(words) != required_words:
            raise _need(
                command,
                words,
                required_words,
                "one identity" if required_words else "no identities",
                "一个标识" if required_words else "不接受标识",
            )
        if words:
            parameters[identity] = words[0]
        validate(parameters, schema)
        return command, parameters, options

    if words or parameters:
        raise _no_arguments(command, words, parameters)
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
    language_token = None
    try:
        argv, chosen_language = i18n.take_argument(argv)
        if chosen_language is not None:
            language_token = i18n.set_language(chosen_language)
        if (
            renderer.mode == "human"
            and stdout.isatty()
            and sys.stdin.isatty()
            and "--no-interactive" not in argv
        ):
            from imagen_cli.cli.selection import choose

            if (
                argv[:2] in (["skills", "install"], ["skills", "uninstall"])
                and not any(value == "--target" or value.startswith("--target=") for value in argv)
                and not any(value in {"--help", "-h"} for value in argv)
            ):
                from imagen_cli.application.skill_targets import prompt_order

                labels, preselected = prompt_order()
                selected = choose(labels, stdin=sys.stdin, stderr=stderr, selected=preselected)
                if not selected:
                    raise KeyboardInterrupt
                for name in selected:
                    argv.extend(["--target", name])
            if (
                argv[:2] == ["superres", "fetch"]
                and not any(
                    value in {"--help", "-h", "--model"} or value.startswith("--model=")
                    for value in argv
                )
                and not any(not value.startswith("-") for value in argv[2:])
            ):
                from imagen_cli.application.superres import MANIFEST, weights_directory

                labels = [
                    f"{name}: {row['scale']}x | {row['license']} | {row['source']}"
                    for name, row in MANIFEST.items()
                ]
                stderr.write(
                    i18n.t("Weights directory: ", "权重目录：") + f"{weights_directory()}\n"
                )
                selected = choose(labels, stdin=sys.stdin, stderr=stderr)
                if len(selected) != 1:
                    raise KeyboardInterrupt
                model = selected[0].partition(":")[0]
                stderr.write(
                    i18n.t(
                        "Download size is unknown; review the source and license first. "
                        "Download now? [y/N] ",
                        "下载大小未知；请先查阅来源许可。确认下载？[y/N] ",
                    )
                )
                stderr.flush()
                if sys.stdin.readline().strip().lower() != "y":
                    raise KeyboardInterrupt
                argv.extend(["--model", model])
                if "--accept" not in argv:
                    argv.append("--accept")
        command, args, options = _parse(argv)
        if renderer.mode == "jsonl":
            renderer.start(command)
        if command == "version":
            status, data = "succeeded", {"version": installed_version()}
        else:
            with Container(workspace=options.workspace, options=options) as container:
                status, data = container.execute(command, args)
        value = result(status, data)
    except UsageError as error:
        error.next_command = error.next_command or _command_hint(argv)
        value = result("validation_error", error=error.detail())
    except ValidationError as error:
        usage = describe_validation(error)
        usage.next_command = usage.next_command or _command_hint(argv)
        value = result("validation_error", error=usage.detail())
    except (ValueError, KeyError) as error:
        described = describe_pydantic(error)
        if described is not None:
            described.next_command = described.next_command or _command_hint(argv)
            value = result("validation_error", error=described.detail())
        else:
            value = result("validation_error", error=plain_detail(error))
    except KeyboardInterrupt:
        value = result("canceled")
    except Exception:
        value = result("internal_error")
    try:
        return renderer.finish(value)
    except (BrokenPipeError, OSError):
        # Never rerun the command after an output failure.
        return 1
    finally:
        i18n.reset_language(language_token)


if __name__ == "__main__":
    raise SystemExit(main())
