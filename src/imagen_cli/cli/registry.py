"""Single source for discoverable delivered and planned operations."""

from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any

from imagen_cli.domain.contracts import CreationInput, InvocationEvent, Result

from .options import OPTION_METADATA


@dataclass(frozen=True)
class Command:
    name: str
    summary: str
    milestone: str = "M1"
    available: bool = False


COMMANDS = {
    command.name: command
    for command in (
        Command("help", "查看命令帮助；--agent 返回安全调用手册", available=True),
        Command("schema", "离线查看输入、输出、默认值和交付状态", available=True),
        *(
            Command(name, "尚未交付；不会上传或创建请求")
            for name in (
                "providers",
                "models",
                "capabilities",
                "capabilities refresh",
                "profile add",
                "profile list",
                "profile show",
                "profile update",
                "session create",
                "session list",
                "session show",
                "session resume",
                "generate",
                "edit",
                "job get",
                "job wait",
                "job fetch",
                "job reconcile",
                "job cancel",
            )
        ),
        *(
            Command(name, "尚未交付；不会上传或创建请求", "M3")
            for name in (
                "session fork",
                "session export",
                "session import",
                "session delete",
                "asset show",
                "asset gc",
                "batch submit",
                "batch get",
                "batch wait",
                "plan show",
                "plan approve",
                "plan execute",
                "worker start",
                "worker status",
                "worker recover",
            )
        ),
    )
}


def select_output(argv: list[str], *, is_tty: bool = False) -> str:
    for index, token in enumerate(argv):
        if token.startswith("--output="):
            value = token.partition("=")[2]
        elif token == "--output" and index + 1 < len(argv):
            value = argv[index + 1]
        else:
            continue
        if value in {"human", "json", "jsonl"}:
            return value
    return "human" if is_tty else "json"


def _object(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(required),
    }


def _creation_schema(operation: str | None = None) -> dict[str, Any]:
    schema = CreationInput.model_json_schema()
    properties = schema["properties"]
    # Pydantic's runtime validators are not represented in its generated schema.
    for field in ("profile", "model", "mask", "size", "retry_of_job_id", "ack_duplicate_cost"):
        properties[field] = properties[field]["anyOf"][0]
    schema["allOf"] = [
        {
            "if": {"properties": {"operation": {"const": "edit"}}},
            "then": {"required": ["inputs"], "properties": {"inputs": {"minItems": 1}}},
        },
        {
            "if": {"required": ["retry_of_job_id"]},
            "then": {
                "required": ["ack_duplicate_cost"],
                "properties": {"ack_duplicate_cost": {"const": True}},
            },
        },
        {"if": {"required": ["ack_duplicate_cost"]}, "then": {"required": ["retry_of_job_id"]}},
    ]
    if operation is not None:
        properties["operation"] = {"const": operation}
    return schema


def _input_schema(name: str) -> dict[str, Any]:
    text = {"type": "string", "minLength": 1}
    properties: dict[str, Any] = {}
    required: tuple[str, ...] = ()
    if name in {"generate", "edit"}:
        return _creation_schema(name)
    if name in {"help", "schema"}:
        properties = {"command": {"enum": list(COMMANDS)}}
        if name == "help":
            properties["agent"] = {"type": "boolean", "default": False}
    elif name.split()[0] in {"providers", "models", "capabilities"}:
        properties = dict.fromkeys(("profile", "model", "operation"), text)
    elif name.startswith("profile "):
        if name != "profile list":
            properties = {"profile": text}
            required = ("profile",)
        if name in {"profile add", "profile update"}:
            properties["config_file"] = text
            required += ("config_file",)
    elif name == "session create":
        properties = dict.fromkeys(("name", "intent_file", "profile", "model"), text)
        required = ("name",)
    elif name.startswith("session ") and name not in {"session list", "session import"}:
        properties = {"session": text}
        required = ("session",)
        if name == "session fork":
            properties.update(dict.fromkeys(("from_turn", "artifact", "name"), text))
            required += ("from_turn", "name")
        elif name == "session export":
            properties["to"] = text
            required += ("to",)
        elif name == "session delete":
            properties["yes"] = {"const": True}
            required += ("yes",)
    elif name == "session import":
        properties, required = {"archive": text}, ("archive",)
    elif name.startswith(("job ", "batch ", "plan ")) and name != "batch submit":
        entity = name.split()[0]
        field = entity + "_id"
        prefix = {"job": "job", "batch": "bat", "plan": "pln"}[entity]
        properties = {field: {"type": "string", "pattern": f"^{prefix}_[0-9a-f]{{32}}$"}}
        required = (field,)
        if name.endswith(" wait"):
            properties["timeout"] = OPTION_METADATA["timeout"]
        if name == "job cancel":
            properties["scope"] = {"enum": ["wait", "local", "remote"]}
            required += ("scope",)
        if name == "plan approve":
            properties["digest"] = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
            required += ("digest",)
    elif name == "batch submit":
        creation = _creation_schema()
        properties = {
            "requests": {"type": "array", "minItems": 1, "maxItems": 100, "items": creation},
            "concurrency": {"type": "integer", "minimum": 1, "maximum": 8, "default": 2},
        }
        schema = _object(properties, ("requests",))
        # Nested creation references still resolve against the document root.
        schema["$defs"] = creation.pop("$defs")
        return schema
    elif name == "asset show":
        properties = {"artifact": {"type": "string", "pattern": "^art_[0-9a-f]{32}$"}}
        required = ("artifact",)
    elif name == "asset gc":
        properties = {
            "dry_run": {"type": "boolean", "default": False},
            "plan": text,
            "yes": {"const": True},
        }
    if name in {"providers", "models", "capabilities", "profile list", "session list"}:
        properties.update({key: OPTION_METADATA[key] for key in ("limit", "cursor")})
    return _object(properties, required)


def _data_schema(command: Command) -> dict[str, Any]:
    if not command.available:
        # Only the actual unsupported result is advertised, not hypothetical business output.
        return _object({})
    return _object(
        {
            "commands": {"type": "array", "items": {"type": "object"}},
            "options": {"type": "object"},
            **dict.fromkeys(
                ("input_schema", "data_schema", "output_schema", "event_schema", "limits"),
                {"type": "object"},
            ),
            "manual": {"type": "string"},
        },
        ("commands", "options", "input_schema", "data_schema", "output_schema",
         "event_schema", "limits", "manual"),
    )


def discover(name: str | None = None, *, agent: bool = False) -> dict[str, Any]:
    if name is not None and name not in COMMANDS:
        raise KeyError(name)
    commands = [COMMANDS[name]] if name else list(COMMANDS.values())
    records = []
    for command in commands:
        data_schema = _data_schema(command)
        output_schema = Result.model_json_schema()
        output_schema["properties"]["data"] = deepcopy(data_schema)
        records.append({
            **asdict(command),
            "input_schema": _input_schema(command.name),
            "data_schema": data_schema,
            "output_schema": output_schema,
        })
    selected = records[0] if name else None
    return deepcopy({
        "commands": records,
        "options": OPTION_METADATA,
        "input_schema": selected["input_schema"] if selected else _input_schema("schema"),
        "data_schema": selected["data_schema"] if selected else _data_schema(COMMANDS["schema"]),
        "output_schema": selected["output_schema"] if selected else Result.model_json_schema(),
        "event_schema": InvocationEvent.model_json_schema(),
        "limits": {"text_bytes": 1048576, "json_bytes": 4194304, "stdin_seconds": 30},
        "manual": (
            "仅显式输入才读取stdin；先查询schema。未交付命令返回unsupported_capability。"
            "input_schema描述规划的结构化语义，不代表CLI参数已实现或供应商支持。"
            "未交付命令data_schema只描述当前空错误数据。"
            "unknown不自动重试创建；--yes不是跨服务授权。"
        )
        if agent
        else "",
    })
