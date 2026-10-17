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
                "capabilities refresh",
                "profile add",
                "profile list",
                "profile show",
                "profile update",
                "job cancel",
            )
        ),
        *(
            Command(name, summary, available=True)
            for name, summary in (
                ("generate", "生成本地执行计划并要求批准；尚不发送供应商请求"),
                ("edit", "冻结输入并生成本地编辑计划；尚不发送供应商请求"),
                ("plan show", "查看持久执行计划；不发送请求"),
                ("plan approve", "显式批准计划摘要；不发送请求"),
                ("plan execute", "执行已批准计划；最多发送一次供应商创建请求"),
                ("providers", "查询本地 profile 的供应商；不联网探测"),
                ("models", "查询本地声明的模型与修订；不联网探测"),
                ("capabilities", "查询本地能力与证据状态；不刷新或升级证据"),
                ("profile add", "新增本地 profile；只保存 credential_ref"),
                ("profile list", "列出本地 profile"),
                ("profile show", "查看本地 profile"),
                ("profile update", "创建新的 profile revision"),
                ("profile set", "以命令行参数创建或更新 profile；接受 env: 或 keyring: 引用"),
                ("session create", "创建本地创作会话"),
                ("session list", "分页列出本地会话"),
                ("session show", "查看本地会话历史"),
                ("session resume", "恢复本地会话快照；不发送请求"),
                ("session fork", "从指定 turn 与产物分支新会话；不发送请求"),
                ("session export", "导出不含秘密的可移植 ZIP；不发送请求"),
                ("session import", "校验 ZIP 并事务导入，重映射全部标识"),
                ("session delete", "逻辑删除 session；需显式 --yes"),
                ("job get", "查看持久任务事实"),
                ("job wait", "有界等待本地任务状态；不轮询供应商"),
                ("job fetch", "从本地持久响应补存内联图片或安全下载结果；不重新生成"),
                ("job reconcile", "核对本地 staging 并恢复图片；不重新生成"),
                ("job cancel", "wait 停止观察；local 校验执行者身份后请求终止；remote 未开放"),
                ("worker status", "查看持久 worker 与启动身份；不派发创建"),
                ("worker start", "在支持的平台启动隔离 worker 并完成握手"),
                ("worker recover", "仅接管过期租约做观察/恢复；不重发创建"),
                ("batch submit", "离线校验全部子请求后按并发上限执行；每个子项独立 job"),
                ("batch get", "查看批次汇总与全部子任务事实；不改变子任务"),
                ("batch wait", "有界等待批次汇总；不重试子任务"),
                ("asset gc", "dry-run 列出未被引用的受管产物；--plan --yes 仅确认该清单"),
                ("cache audit", "列出可回收空间与字节数；不删除"),
                ("cache purge", "凭清单摘要确认后回收；默认 dry-run"),
                ("models refresh", "从 profile 端点拉取模型列表；只写 provider_models"),
                ("models add", "手工声明一个模型，evidence=manual"),
                ("models list", "列出 profile 的已声明模型"),
                ("transform", "对产物应用确定性变换并生成新产物；不覆盖源文件"),
                ("compare", "计算两个产物的 PSNR 与 SSIM；只读，不创建新产物"),
                ("sheet", "把多个产物拼成联系表并生成新产物"),
                ("ops", "列出可用的确定性变换操作"),
                ("superres models", "列出超分模型、许可、下载与安装状态"),
                ("superres fetch", "按固定摘要下载超分权重；需 --accept"),
                ("superres benchmark", "用降采样自洽比较多个超分模型的 PSNR/SSIM 与耗时"),
                ("tui", "打开终端控制面板；只读浏览与确认式删除"),
            )
        ),
        *(
            Command(name, "尚未交付；不会上传或创建请求", "M3")
            for name in ("asset show",)
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
    elif name.startswith("models "):
        properties = {"profile": text}
        required = ("profile",)
        if name == "models refresh":
            properties["send_credential"] = {"type": "boolean", "default": False}
        elif name == "models add":
            properties["id"] = text
            required += ("id",)
    elif name.split()[0] in {"providers", "models", "capabilities"}:
        properties = dict.fromkeys(("profile", "model", "operation"), text)
    elif name.startswith("profile "):
        if name != "profile list":
            properties = {"profile": text}
            required = ("profile",)
        if name in {"profile add", "profile update"}:
            properties["config_file"] = text
            required += ("config_file",)
        elif name == "profile set":
            properties.update(dict.fromkeys(
                ("provider", "family", "base_url", "model_default", "credential_ref"), text,
            ))
            properties["revision"] = {"type": "integer", "minimum": 1, "default": 1}
            required += ("provider", "family", "base_url", "model_default", "credential_ref")
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
    elif name.startswith("cache "):
        if name == "cache purge":
            properties = {
                "plan": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "yes": {"const": True},
            }
    elif name.startswith(("job ", "batch ", "plan ")) and name != "batch submit":
        entity = name.split()[0]
        field = entity + "_id"
        prefix = {"job": "job", "batch": "bat", "plan": "pln"}[entity]
        properties = {field: {"type": "string", "pattern": f"^{prefix}_[0-9a-f]{{32}}$"}}
        required = (field,)
        if name.endswith(" wait") or name == "plan execute":
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
    elif name == "transform":
        properties = {
            "artifact": {"type": "string", "pattern": "^art_[0-9a-f]{32}$"},
            "pipeline": text,
            "op": text,
            "session": text,
        }
        required = ("artifact",)
    elif name == "sheet":
        properties = {
            "artifacts": {"type": "string", "minLength": 1},
            "session": text,
            "columns": {"type": "integer", "minimum": 1, "maximum": 10, "default": 3},
            "cell": {"type": "integer", "minimum": 32, "maximum": 2048, "default": 256},
        }
        required = ("artifacts",)
    elif name == "ops":
        properties = {}
        required = ()
    elif name == "superres models":
        properties = {}
        required = ()
    elif name == "superres fetch":
        properties = {"model": text, "accept": {"const": True}}
        required = ("model", "accept")
    elif name == "superres benchmark":
        properties = {
            "artifact": {"type": "string", "pattern": "^art_[0-9a-f]{32}$"},
            "model": {"type": "string", "minLength": 1},
            "backend": text,
            "tile": {"type": "integer", "minimum": 32, "maximum": 2048, "default": 256},
            "overlap": {"type": "integer", "minimum": 0, "maximum": 256, "default": 16},
        }
        required = ("artifact", "model")
    elif name == "compare":
        properties = {
            "first": {"type": "string", "pattern": "^art_[0-9a-f]{32}$"},
            "second": {"type": "string", "pattern": "^art_[0-9a-f]{32}$"},
        }
        required = ("first", "second")
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
    if command.name == "transform":
        return _object({"artifact": {"type": "object"}, "source": {"type": "string"}})
    if command.name == "sheet":
        return _object({
            "artifact": {"type": "object"},
            "sources": {"type": "array", "items": {"type": "object"}},
        }, ("artifact", "sources"))
    if command.name == "superres models":
        return _object({"models": {"type": "array", "items": {"type": "object"}},
                        "providers": {"type": "array", "items": {"type": "string"}},
                        "model_dir": {"type": "string"}},
                       ("models", "providers", "model_dir"))
    if command.name == "superres fetch":
        return _object({"model": {"type": "string"}, "path": {"type": "string"},
                        "bytes": {"type": "integer"}, "sha256": {"type": "string"}},
                       ("model", "path", "bytes", "sha256"))
    if command.name == "superres benchmark":
        return _object({"artifact": {"type": "string"},
                        "reference": {"type": "array"},
                        "results": {"type": "array", "items": {"type": "object"}}},
                       ("artifact", "reference", "results"))
    if command.name == "ops":
        return _object({"operations": {"type": "array", "items": {"type": "string"}}},
                       ("operations",))
    if command.name == "compare":
        return _object({
            "first": {"type": "string"}, "second": {"type": "string"},
            "width": {"type": "integer"}, "height": {"type": "integer"},
            "identical": {"type": "boolean"}, "psnr": {"type": ["number", "null"]},
            "ssim": {"type": "number"},
        }, ("first", "second", "identical", "psnr", "ssim"))
    if command.name.startswith("models "):
        return _object({
            "models": {"type": "array", "items": {"type": "object"}},
            "model": {"type": "object"},
        })
    if command.name == "cache audit":
        return _object({
            "entries": {"type": "array", "items": {"type": "object"}},
            "plan_digest": {"type": "string"},
            "planned_bytes": {"type": "integer"},
            "count": {"type": "integer"},
        }, ("entries", "plan_digest", "planned_bytes", "count"))
    if command.name == "cache purge":
        return _object({
            "dry_run": {"type": "boolean"},
            "entries": {"type": "array", "items": {"type": "object"}},
            "plan_digest": {"type": "string"},
            "planned_bytes": {"type": "integer"},
            "deleted_bytes": {"type": "integer"},
            "results": {"type": "array", "items": {"type": "object"}},
        }, ("dry_run",))
    if command.name == "plan execute":
        return _object({
            "job": {"type": "object"}, "turn": {"type": "object"},
            "artifacts": {"type": "array", "items": {"type": "object"}},
        }, ("job", "turn", "artifacts"))
    if command.name in {"generate", "edit"}:
        return _object({
            "plan": {"type": "object"},
            "job": {"type": "object"},
            "turn": {"type": "object"},
        })
    if command.name == "worker status":
        return _object({"workers": {"type": "array", "items": {"type": "object"}}}, ("workers",))
    if command.name == "worker start":
        return _object({"worker": {"type": "object"}}, ("worker",))
    if command.name == "worker recover":
        return _object(
            {"recovered": {"type": "array", "items": {"type": "object"}}}, ("recovered",)
        )
    if command.name == "plan show":
        return _object({"plan": {"type": "object"}}, ("plan",))
    if command.name == "plan approve":
        return _object({"approval": {"type": "object"}}, ("approval",))
    if command.name.startswith("profile "):
        if command.name == "profile list":
            return _object({
                "profiles": {"type": "array", "items": {"type": "object"}},
                "next_cursor": {"type": ["string", "null"]},
            }, ("profiles", "next_cursor"))
        return _object({"profile": {"type": "object"}}, ("profile",))
    if command.name in {"providers", "models", "capabilities"}:
        return _object({
            "items": {"type": "array", "items": {"type": "object"}},
            "next_cursor": {"type": ["string", "null"]},
            "evidence": {"const": "offline_persisted"},
        }, ("items", "next_cursor", "evidence"))
    if command.name == "session fork":
        return _object({"session": {"type": "object"}}, ("session",))
    if command.name == "session export":
        return _object({
            "archive": {"type": "string"},
            "session_id": {"type": "string"},
            "turns": {"type": "integer"},
            "artifacts": {"type": "integer"},
            "references": {"type": "integer"},
            "omissions": {"type": "array", "items": {"type": "object"}},
        }, ("archive", "session_id", "turns", "artifacts", "references", "omissions"))
    if command.name == "session import":
        return _object({
            "session": {"type": "object"},
            "turns": {"type": "integer"},
            "artifacts": {"type": "integer"},
            "references": {"type": "integer"},
            "origin_map": {"type": "object"},
            "omissions": {"type": "array", "items": {"type": "object"}},
            "native_contexts_missing": {"type": "array", "items": {"type": "object"}},
        }, ("session", "turns", "artifacts", "references", "origin_map", "omissions",
            "native_contexts_missing"))
    if command.name == "session delete":
        return _object({
            "session": {"type": "object"},
            "deleted": {"type": "boolean"},
            "exported_files_untouched": {"type": "boolean"},
        }, ("session", "deleted", "exported_files_untouched"))
    if command.name == "asset gc":
        return _object({
            "dry_run": {"type": "boolean"},
            "plan": {"type": "string"},
            "candidates": {"type": "array", "items": {"type": "object"}},
            "deleted": {"type": "array", "items": {"type": "string"}},
        }, ("dry_run", "plan", "candidates", "deleted"))
    if command.name.startswith("session "):
        row = {"type": "object"}
        rows = {"type": "array", "items": row}
        if command.name == "session list":
            return _object(
                {"sessions": rows, "next_cursor": {"type": ["string", "null"]}},
                ("sessions", "next_cursor"),
            )
        properties: dict[str, Any] = {"session": row}
        if command.name in {"session show", "session resume"}:
            properties.update({"turns": rows, "references": rows})
        required = tuple(properties)
        if command.name == "session resume":
            properties["next_actions"] = rows
            required += ("next_actions",)
        return _object(properties, required)
    if command.name.startswith("batch "):
        return _object(
            {
                "batch": {"type": "object"},
                "summary": {"type": "object"},
                "children": {"type": "array", "items": {"type": "object"}},
                "events": {"type": "array", "items": {"type": "object"}},
            },
            ("batch", "summary", "children", "events"),
        )
    if command.name.startswith("job "):
        return _object(
            {
                "job": {"type": "object"},
                "artifacts": {"type": "array", "items": {"type": "object"}},
                "next_actions": {"type": "array", "items": {"type": "object"}},
            },
            ("job", "artifacts", "next_actions"),
        )
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
        if command.available and command.name not in {"help", "schema"}:
            output_schema["properties"]["data"] = {
                "anyOf": [deepcopy(data_schema), _object({})],
            }
            output_schema["allOf"] = [{
                "if": {"properties": {"ok": {"const": True}}},
                "then": {"properties": {"data": deepcopy(data_schema)}},
            }]
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
