"""Single source for discoverable delivered and planned operations."""

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from typing import Any

from imagen_cli import i18n
from imagen_cli.domain.contracts import CreationInput, InvocationEvent, Result
from imagen_cli.providers import vocabulary

from .options import OPTION_METADATA


@dataclass(frozen=True)
class Command:
    name: str
    summary: str
    milestone: str = "M1"
    available: bool = False
    summary_en: str = ""


COMMANDS = {
    command.name: command
    for command in (
        Command("help", "查看命令帮助；--agent 返回安全调用手册", available=True),
        Command("schema", "离线查看输入、输出、默认值和交付状态", available=True),
        *(
            Command(name, "尚未交付；不会上传或创建请求")
            for name in (
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
                ("generate", "生成执行计划并要求批准；--async 入队由本地 worker 执行"),
                ("edit", "冻结输入并生成编辑计划；--async 入队由本地 worker 执行"),
                ("plan show", "查看持久执行计划；不发送请求"),
                ("plan approve", "显式批准计划摘要；不发送请求"),
                ("plan execute", "执行已批准计划；最多发送一次供应商创建请求"),
                ("plan retry", "按原计划重建一次新请求，生成新的待批准计划"),
                ("providers", "查询本地 profile 的供应商；不联网探测"),
                ("models", "查询本地声明的模型与修订；不联网探测"),
                ("profile add", "新增本地 profile；只保存 credential_ref"),
                ("profile list", "列出本地 profile"),
                ("profile show", "查看本地 profile"),
                ("profile update", "创建新的 profile revision"),
                ("profile set", "以命令行参数创建或更新 profile；接受 env:/keyring:/private: 引用"),
                ("profile secret", "把 --key-file 的密钥写入私有存储并更新 credential_ref；不回显"),
                ("session create", "创建本地创作会话"),
                ("session list", "分页列出本地会话"),
                ("session show", "查看本地会话历史"),
                ("session resume", "恢复本地会话快照；不发送请求"),
                ("session fork", "从指定 turn 与产物分支新会话；不发送请求"),
                ("session export", "导出不含秘密的可移植 ZIP；不发送请求"),
                ("session import", "校验 ZIP 并事务导入，重映射全部标识"),
                ("session import-image", "把本地图片或视频导入会话并生成新产物；不覆盖原文件"),
                ("session delete", "逻辑删除 session；需显式 --yes"),
                ("session remove-artifact", "从会话中移除指定的产物图片"),
                ("session remove-image", "从会话中移除指定的产物图片（同 remove-artifact）"),
                ("job get", "查看持久任务事实"),
                ("job wait", "有界等待本地任务状态；不轮询供应商"),
                ("job fetch", "从本地持久响应补存内联图片或安全下载结果；不重新生成"),
                ("job reconcile", "核对本地 staging 并恢复图片；不重新生成"),
                ("job cancel", "wait 停止观察；local 校验执行者身份后请求终止；remote 未开放"),
                ("worker status", "查看持久 worker 与启动身份；不派发创建"),
                ("worker start", "在支持的平台启动隔离 worker，执行 async 队列并维持心跳"),
                ("worker recover", "仅接管过期租约做观察/恢复；不重发创建"),
                ("batch submit", "离线校验全部子请求后按并发上限执行；每个子项独立 job"),
                ("batch get", "查看批次汇总与全部子任务事实；不改变子任务"),
                ("batch wait", "有界等待批次汇总；不重试子任务"),
                ("asset gc", "dry-run 列出未被引用的受管产物；--plan --yes 仅确认该清单"),
                ("asset show", "查看单个产物的路径、归属与来源事实"),
                ("asset delete", "永久删除指定产物实体并清理本地文件"),
                ("asset remove", "永久删除指定产物实体（同 asset delete）"),
                ("asset open", "用系统默认程序打开产物图片，或 --folder 打开所在目录"),
                ("asset save", "把产物另存到指定路径；不覆盖已有文件"),
                ("cache audit", "列出可回收空间与字节数；不删除"),
                ("cache purge", "凭清单摘要确认后回收；默认 dry-run"),
                ("skills list", "列出可安装的 agent skill"),
                ("skills install", "把 agent skill 安装到当前项目或全局"),
                ("skills update", "把已安装的 agent skill 更新到当前版本"),
                ("skills uninstall", "卸载 Imagen 管理的 agent skill"),
                ("update", "检查是否有新版本；只读网络请求，不安装"),
                ("models refresh", "从 profile 端点拉取模型列表；只写 provider_models"),
                ("models add", "手工声明一个模型，evidence=manual"),
                ("models use", "把一个模型设为 profile 默认；未声明时按 manual 登记"),
                ("models list", "列出 profile 的已声明模型"),
                ("transform", "对产物应用确定性变换并生成新产物；不覆盖源文件"),
                ("compare", "计算两个产物的 PSNR 与 SSIM；只读，不创建新产物"),
                ("sheet", "把多个产物拼成联系表并生成新产物"),
                ("ops", "列出可用的确定性变换操作"),
                ("superres run", "独立本地模型处理：恢复或超分，保存新产物；超预算先确认"),
                ("superres models", "列出超分模型、许可、下载与安装状态"),
                ("superres doctor", "用一个 32×32 图跑一 tile，证明请求的 backend 真的生效"),
                ("superres install", "装运行时与所选模型；不给 --model 只装运行时；需 --accept"),
                ("superres fetch", "按固定摘要下载超分权重；需 --accept"),
                ("superres benchmark", "用降采样自洽比较多个超分模型的 PSNR/SSIM 与耗时"),
                ("matting run", "本地抠图：生成带 alpha 的新产物；可链式做贴纸或虚化"),
                ("matting models", "列出抠图模型、许可、下载与安装状态"),
                ("matting doctor", "用 64×64 图跑一次推理，证明请求的 backend 真的生效"),
                ("matting install", "装运行时与所选抠图模型；需 --accept"),
                ("matting fetch", "按固定摘要下载抠图权重；需 --accept"),
                ("watermark identify", "只读检查 C2PA/EXIF/XMP/IPTC/TC260 与可见、不可见标记"),
                ("watermark metadata", "复制产物并移除 AI 元数据；--remove 才写入新产物"),
                ("watermark visible", "复制产物并移除已注册的可见 AI 标记"),
                ("watermark erase", "按 --region 擦除指定矩形区域并复制产物；不覆盖原图"),
                ("watermark all", "依次移除可见、不可见与元数据水印并生成新产物；需许可证确认"),
                ("watermark classify", "只读按像素分类来源或拍摄属性；不是来源证明"),
                ("watermark batch", "按目录处理图片并写入新目录；不覆盖源目录；支持四种模式"),
                ("watermark video identify", "只读检查视频容器中的 AI 元数据与稳定可见标记"),
                ("watermark video metadata", "复制视频并移除 AI 元数据；--remove 才写入新产物"),
                ("watermark video visible", "复制视频并移除已注册的可见标记；无匹配时不写产物"),
                ("watermark video all", "复制视频并依次移除可见标记与 AI 元数据；可选像素重建"),
                ("watermark video invisible", "用共享噪声场重建视频像素以破坏 SynthID 类水印"),
                ("watermark video batch", "按目录处理视频并写入新目录；不覆盖源目录"),
                ("watermark invisible", "用扩散重建移除不可见水印；需 GPU；超内存预算先确认"),
                ("watermark resample", "降采样后用超分重建像素；只破坏像素统计，不证明水印已移除"),
                ("watermark status", "报告隔离 raiw 环境、版本、能力与内存上限；不打印密钥"),
                ("watermark setup", "预览或创建隔离 raiw 环境；需 --accept 才安装"),
                ("watermark update", "新建并验证隔离 raiw 环境后切换；失败保留旧环境；需 --accept"),
                ("remote add", "登记远程 GPU 端点；探测 /v1/meta 后写入 config.json"),
                ("remote list", "列出已登记的远程端点；不打印凭据"),
                ("remote status", "读取端点状态并刷新登记的 GPU 与 worker digest"),
                ("remote remove", "移除一个远程端点登记"),
                ("remote jobs list", "列出本地记录的远程 job；可按 provider 过滤"),
                ("remote job get", "读取远程 job 的本地记录与实时状态"),
                ("remote job resume", "重新读取 queued/running/succeeded job 并取回结果"),
                ("remote job retry-interrupted", "仅对 interrupted job 以同一 digest 重新入队"),
                ("remote job cancel", "尽力取消远程 job；结果如实记录"),
                ("deploy status", "只读检查 Salad/Modal worker 的镜像、就绪与登记漂移"),
                ("deploy update", "按已发布镜像更新 worker 或改名重建 Salad 组；需 --accept"),
                ("deploy build", "显式构建 worker 或 Salad 队列镜像；需 --accept"),
                ("deploy remove", "删除 Salad 容器组或停止 Modal 应用；需 --accept --yes"),
                ("deploy secret set", "把部署密钥写入私有存储并只返回 private: 引用"),
                ("language", "查看或设置默认界面语言"),
                ("tui", "打开终端控制面板；只读浏览与确认式删除"),
            )
        ),
    )
}


_SUMMARY_EN = {
    "help": "Show command help; --agent returns the safe-call manual",
    "schema": "Inspect input, output, defaults and delivery status offline",
    "generate": "Create an execution plan requiring approval; --async queues it for the "
    "local worker",
    "edit": "Freeze inputs and create an edit plan; --async queues it for the local worker",
    "plan show": "Show a persisted execution plan; sends no request",
    "plan approve": "Approve a plan digest explicitly; sends no request",
    "plan execute": "Execute an approved plan; sends at most one provider creation request",
    "plan retry": "Rebuild one new request from the original plan as a new plan awaiting approval",
    "providers": "Show providers for local profiles; no network probe",
    "models": "Show locally declared models and revisions; no network probe",
    "profile add": "Add a local profile; stores only credential_ref",
    "profile list": "List local profiles",
    "profile show": "Show a local profile",
    "profile update": "Create a new profile revision",
    "profile set": "Create or update a profile from CLI arguments; accepts "
    "env:/keyring:/private: references",
    "profile secret": "Store the --key-file secret in private storage and update "
    "credential_ref; never echoes it",
    "session create": "Create a local creation session",
    "session list": "List local sessions with pagination",
    "session show": "Show local session history",
    "session resume": "Restore a local session snapshot; sends no request",
    "session fork": "Branch a new session from a turn and artifact; sends no request",
    "session export": "Export a portable ZIP without secrets; sends no request",
    "session import": "Validate a ZIP and import it transactionally with remapped identities",
    "session import-image": "Import a local image into a session as a new artifact; "
    "never overwrites the source file",
    "session delete": "Soft-delete a session; requires explicit --yes",
    "session remove-artifact": "Remove the given artifact image from a session",
    "session remove-image": "Remove the given artifact image from a session "
    "(same as remove-artifact)",
    "job get": "Show persisted job facts",
    "job wait": "Wait for local job state with a bound; does not poll the provider",
    "job fetch": "Save inline images or safely download results from a persisted response; "
    "does not regenerate",
    "job reconcile": "Reconcile local staging and restore images; does not regenerate",
    "job cancel": "wait stops observing; local verifies the executor identity before "
    "requesting termination; remote is not available",
    "worker status": "Show the persisted worker and launch identity; dispatches no creation",
    "worker start": "Start an isolated worker on supported platforms to run the async queue "
    "and keep a heartbeat",
    "worker recover": "Take over expired leases for observation/recovery only; "
    "never resends creation",
    "batch submit": "Validate every child request offline, then execute up to the concurrency "
    "limit; each child is an independent job",
    "batch get": "Show the batch summary and every child job's facts; does not change children",
    "batch wait": "Wait for the batch summary with a bound; does not retry children",
    "asset gc": "Dry-run lists unreferenced managed artifacts; --plan --yes confirms only "
    "that list",
    "asset show": "Show one artifact's path, ownership and provenance facts",
    "asset delete": "Permanently delete the given artifact and clean up local files",
    "asset remove": "Permanently delete the given artifact (same as asset delete)",
    "asset open": "Open the artifact with the system default application, or its folder "
    "with --folder",
    "asset save": "Save the artifact to a chosen path; never replaces an existing file",
    "cache audit": "List reclaimable entries and bytes; deletes nothing",
    "cache purge": "Reclaim after confirming the inventory digest; dry-run by default",
    "skills list": "List installable agent skills",
    "skills install": "Install agent skills into the current project or globally",
    "skills update": "Update installed agent skills to the bundled version",
    "update": "Check whether a newer release exists; installs nothing",
    "skills uninstall": "Uninstall agent skills managed by Imagen",
    "models refresh": "Fetch the model list from the profile endpoint; writes provider_models only",
    "models add": "Declare a model manually with evidence=manual",
    "models use": "Set a model as the profile default; registers it as manual when undeclared",
    "models list": "List the profile's declared models",
    "transform": "Apply a deterministic transform and create a new artifact; "
    "never overwrites the source",
    "compare": "Compute PSNR and SSIM for two artifacts; read-only, creates no artifact",
    "sheet": "Compose artifacts into a contact sheet as a new artifact",
    "ops": "List available deterministic transform operations",
    "superres models": "List upscaling models, licenses, download and install state",
    "superres doctor": "Run one tile on a 32x32 image to prove the requested backend "
    "is really in use; no artifact, no weight download",
    "superres install": "Install the inference runtime and any named models; "
    "without --model it installs only the runtime; requires --accept",
    "superres run": "Run local model restoration or upscaling; save a new artifact, "
    "asking for approval when resource budgets are exceeded",
    "superres fetch": "Download upscaling weights pinned to a digest; requires --accept",
    "superres benchmark": "Compare upscaling models by downsampling self-consistency, "
    "PSNR/SSIM and time",
    "matting run": "Predict an alpha matte locally and save an RGBA artifact; the source "
    "is never modified",
    "matting models": "List matting models with licence, download and install state",
    "matting doctor": "Run one inference on a 64x64 image to prove the requested backend "
    "is really in use; no weight download",
    "matting install": "Install the inference runtime and any named matting models; "
    "requires --accept",
    "matting fetch": "Download matting weights pinned to a digest; requires --accept",
    "watermark identify": "Read-only check of C2PA, EXIF, XMP, IPTC, TC260 and visible "
    "or invisible marks; creates no artifact",
    "watermark metadata": "Remove AI metadata from a copy; --remove writes a new artifact",
    "watermark visible": "Remove registered visible AI marks into a new artifact",
    "watermark erase": "Erase explicit --region boxes into a new artifact; keeps the source",
    "watermark all": "Run visible, invisible and metadata removal in order as a new artifact; "
    "requires license confirmation",
    "watermark classify": "Classify a photo or its source pipeline from pixels; read-only, "
    "not provenance",
    "watermark batch": "Process a directory of images into a new directory; never overwrites "
    "the source directory; visible, metadata, all or invisible",
    "watermark video identify": "Read-only check of video AI metadata and stable visible marks",
    "watermark video metadata": "Copy a video and strip AI metadata; --remove writes an artifact",
    "watermark video visible": "Copy a video and remove a stable visible mark; writes nothing "
    "when no mark matches",
    "watermark video all": "Copy a video and remove visible marks plus AI metadata; optional "
    "pixel regeneration",
    "watermark video invisible": "Regenerate video pixels with a shared noise field to disrupt "
    "SynthID-class marks; lossy",
    "watermark video batch": "Process a directory of videos into a new directory; never "
    "overwrites the source directory",
    "watermark invisible": "Regenerate pixels to disrupt invisible watermarks; needs a GPU; "
    "an over-budget run asks first",
    "watermark resample": "Downscale then upscale to rebuild pixels; new artifact, and "
    "no claim that the watermark is gone",
    "watermark status": "Report the isolated raiw environment, version, capabilities and "
    "memory limit; prints no secret",
    "watermark setup": "Preview or create the isolated raiw environment; installs only "
    "with --accept",
    "watermark update": "Build and verify a new isolated raiw environment, then switch; "
    "the old environment survives a failed probe; requires --accept",
    "remote add": "Register a remote GPU endpoint; probes /v1/meta before writing config",
    "remote list": "List registered remote endpoints; prints no credential",
    "remote status": "Read endpoint status and refresh its recorded GPU and worker digest",
    "remote remove": "Remove one remote endpoint registration",
    "remote jobs list": "List locally recorded remote jobs; filter by provider",
    "remote job get": "Read a remote job's local record and live status",
    "remote job resume": "Re-read a queued, running or succeeded job and fetch its result",
    "remote job retry-interrupted": "Re-enqueue only an interrupted job under the same digest",
    "remote job cancel": "Best-effort cancel; the recorded result is reported as-is",
    "deploy status": "Read-only check of the Salad/Modal worker image, readiness and "
    "registration drift",
    "deploy update": "Update a worker from a published image, or recreate a Salad group "
    "under a new name; requires --accept",
    "deploy build": "Build the worker image, or the Salad queue image on top of it; "
    "explicit only; requires --accept",
    "deploy remove": "Delete a Salad container group or stop the Modal app; "
    "irreversible; requires --accept and --yes",
    "deploy secret set": "Store a deployment key privately and return only its "
    "private: reference",
    "language": "Show or set the default interface language",
    "tui": "Open the terminal workspace; browse read-only and delete with confirmation",
}
for _name, _summary_en in _SUMMARY_EN.items():
    COMMANDS[_name] = replace(COMMANDS[_name], summary_en=_summary_en)


def command_summary(name: str) -> str:
    """Display summary in the active language; the command name never changes."""
    command = COMMANDS[name]
    if not i18n.is_chinese() and command.summary_en:
        return command.summary_en
    return command.summary


def select_output(argv: list[str], *, is_tty: bool = False) -> str:
    """Humans get rich output by default; --output/--agent opt into machine modes."""
    for index, token in enumerate(argv):
        if token.startswith("--output="):
            value = token.partition("=")[2]
        elif token == "--output" and index + 1 < len(argv):
            value = argv[index + 1]
        else:
            continue
        if value in {"human", "json", "jsonl"}:
            return value
    if "--agent" in argv:
        return "json"
    return "human"


def _object(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(required),
    }


def _geometry_descriptions() -> dict[str, str]:
    """Help text for the geometry fields, built from the shared vocabulary."""
    gemini = {
        spec.name: spec.values
        for spec in vocabulary.specs(
            "gemini", "generate_content", "gemini-3.1-flash-image", "generate"
        ) or ()
    }
    openai = {
        spec.name: spec.values
        for spec in vocabulary.specs("openai", "images", "gpt-image-2.5-sunburst", "generate")
        or ()
    }
    return {
        "size": (
            "Pixel size for OpenAI image routes: "
            + ", ".join(openai["size"])
            + ", or WIDTHxHEIGHT on gpt-image-2 models (both edges divisible by 16, "
            "aspect ratio 1:3-3:1, no edge above 3840). Gemini routes reject size; "
            "use aspect_ratio and image_size."
        ),
        "aspect_ratio": (
            "Aspect ratio for Gemini image routes: " + ", ".join(gemini["aspect_ratio"]) + "."
        ),
        "image_size": (
            "Output tier for Gemini image routes: "
            + ", ".join(gemini["image_size"])
            + " (uppercase K; each model supports a subset, default 1K)."
        ),
    }


def _creation_schema(operation: str | None = None) -> dict[str, Any]:
    schema = CreationInput.model_json_schema()
    properties = schema["properties"]
    # Pydantic's runtime validators are not represented in its generated schema.
    for field in (
        "profile", "model", "mask", "size", "aspect_ratio", "image_size",
        "retry_of_job_id", "ack_duplicate_cost",
    ):
        properties[field] = properties[field]["anyOf"][0]
    for field, description in _geometry_descriptions().items():
        properties[field]["description"] = description
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


def _superres_models() -> list[str]:
    """The upscaling catalog, read lazily so importing the registry stays cheap."""
    from imagen_cli.application.superres import MANIFEST

    return sorted(MANIFEST)


def _input_schema(name: str) -> dict[str, Any]:
    text = {"type": "string", "minLength": 1}
    _REF = r"^(env:[A-Za-z_][A-Za-z0-9_]*|private:[A-Za-z][A-Za-z0-9_-]{0,127})$"
    properties: dict[str, Any] = {}
    required: tuple[str, ...] = ()
    if name in {"generate", "edit"}:
        return _creation_schema(name)
    if name in {"help", "schema"}:
        properties = {"command": {"enum": list(COMMANDS)}}
        if name == "help":
            properties["agent"] = {"type": "boolean", "default": False}
    elif name == "language":
        properties = {"language": {"enum": ["en", "zh", "zh-CN"]}}
    elif name == "update":
        properties = {"install": {"type": "boolean", "default": False}}
    elif name.startswith("models "):
        properties = {"profile": text}
        required = ("profile",)
        if name == "models refresh":
            properties["send_credential"] = {"type": "boolean", "default": False}
        elif name in {"models add", "models use"}:
            properties["id"] = text
            required += ("id",)
    elif name.split()[0] in {"providers", "models"}:
        properties = dict.fromkeys(("profile", "model"), text)
    elif name.startswith("profile "):
        if name != "profile list":
            properties = {"profile": text}
            required = ("profile",)
        if name in {"profile add", "profile update"}:
            properties["config_file"] = text
            required += ("config_file",)
        elif name == "profile secret":
            properties["key_file"] = text
            required += ("key_file",)
        elif name == "profile set":
            properties.update(
                dict.fromkeys(
                    (
                        "provider",
                        "family",
                        "base_url",
                        "models_path",
                        "model_default",
                        "credential_ref",
                    ),
                    text,
                )
            )
            properties["key_file"] = text
            properties["revision"] = {"type": "integer", "minimum": 1, "default": 1}
            required += ("provider", "family", "base_url", "model_default")
    elif name == "session create":
        properties = dict.fromkeys(("name", "intent", "intent_file", "profile", "model"), text)
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
        elif name in {"session remove-artifact", "session remove-image"}:
            properties["artifact"] = {"type": "string", "pattern": "^art_[0-9a-f]{32}$"}
            properties["purge"] = {"type": "boolean", "default": True}
            required += ("artifact",)
        elif name == "session import-image":
            properties["path"] = text
            required += ("path",)
    elif name == "session import":
        properties, required = {"archive": text}, ("archive",)
    elif name.startswith("cache "):
        if name == "cache purge":
            properties = {
                "plan": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "yes": {"const": True},
            }
    elif name.startswith("skills "):
        properties = {
            "scope": {"enum": ["project", "global"], "default": "project"},
            "skill": {"type": "array", "items": text, "minItems": 1},
            "target": {"type": "array", "items": text, "minItems": 1},
        }
        if name != "skills list":
            properties["force"] = {"type": "boolean", "default": False}
        if name == "skills uninstall":
            properties["yes"] = {"type": "boolean", "default": False}
    elif name.startswith(("job ", "batch ", "plan ")) and name != "batch submit":
        entity = name.split()[0]
        field = entity + "_id"
        prefix = {"job": "job", "batch": "bat", "plan": "pln"}[entity]
        properties = {field: {"type": "string", "pattern": f"^{prefix}_[0-9a-f]{{32}}$"}}
        required = (field,)
        if name.endswith(" wait") or name == "plan execute":
            properties["timeout"] = {"type": "integer", "default": 300,
                                     "minimum": 1, "maximum": 86400}
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
            "preset": text,
            "session": text,
            "timeout": {"type": "number", "minimum": 0},
            "budget_approval": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
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
        properties = {
            "op": text,
            "preset": text,
            "verbose": {"type": "boolean"},
        }
        required = ()
    elif name == "superres run":
        properties = {
            "artifact": {"type": "string", "pattern": "^art_[0-9a-f]{32}$"},
            "model": text, "backend": text, "session": text,
            "tile": {"type": "integer", "minimum": 4, "default": 256},
            "overlap": {"type": "integer", "minimum": 0, "default": 16},
            "accept": {"type": "boolean", "default": False},
            "budget_approval": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "timeout": {"type": "number", "minimum": 0, "default": 900},
        }
        required = ("artifact", "model")
    elif name == "superres models":
        properties = {"verify": {"type": "boolean", "default": False}}
        required = ()
    elif name == "superres doctor":
        properties = {
            "model": {"type": "string", "minLength": 1},
            "backend": {"enum": ["auto", "cpu", "cuda", "openvino"], "default": "auto"},
        }
        required = ()
    elif name == "superres install":
        properties = {
            "model": {"type": "string", "minLength": 1},
            "backend": {"enum": ["cpu", "cuda", "openvino"], "default": "cpu"},
            "accept": {"type": "boolean", "default": False},
        }
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
    elif name == "matting run":
        properties = {
            "artifact": {"type": "string", "pattern": "^art_[0-9a-f]{32}$"},
            "model": {
                "enum": ["birefnet-lite"],
                "default": "birefnet-lite",
            },
            "backend": {"enum": ["auto", "cpu", "cuda", "openvino"], "default": "auto"},
            "tile": {"type": "integer", "minimum": 0, "maximum": 4096, "default": 0},
            "overlap": {"type": "integer", "minimum": 0, "maximum": 1024, "default": 64},
            "feather": {"type": "integer", "minimum": 0, "maximum": 64, "default": 0},
            "edge_shift": {"type": "number", "minimum": -0.5, "maximum": 0.5, "default": 0},
            "accept": {"type": "boolean", "default": False},
            "session": text,
            "remote": {"type": "string", "pattern": "^[a-z][a-z0-9-]{0,31}$"},
            "accept_remote": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "max_seconds": {"type": "integer", "minimum": 1, "maximum": 86400, "default": 900},
            "budget_approval": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "timeout": {"type": "number", "minimum": 0, "default": 900},
        }
        required = ("artifact",)
    elif name == "matting models":
        properties = {"verify": {"type": "boolean", "default": False}}
        required = ()
    elif name == "matting doctor":
        properties = {
            "model": {"type": "string", "minLength": 1},
            "backend": {"enum": ["auto", "cpu", "cuda", "openvino"], "default": "auto"},
        }
        required = ()
    elif name == "matting install":
        properties = {
            "model": {"type": "string", "minLength": 1},
            "backend": {"enum": ["cpu", "cuda", "openvino"], "default": "cpu"},
            "accept": {"type": "boolean", "default": False},
        }
        required = ()
    elif name == "matting fetch":
        properties = {"model": text, "accept": {"const": True}}
        required = ("model", "accept")
    elif name in {"watermark identify", "watermark metadata", "watermark visible"}:
        properties = {
            "artifact": {"type": "string", "pattern": "^art_[0-9a-f]{32}$"},
            "session": text,
            "memory_mb": {"type": "integer", "minimum": 1},
            "remote": {"type": "string", "pattern": "^[a-z][a-z0-9-]{0,31}$"},
            "accept_remote": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "max_seconds": {"type": "integer", "minimum": 1, "maximum": 86400},
        }
        if name == "watermark metadata":
            properties["remove"] = {"type": "boolean", "default": False}
        if name == "watermark visible":
            properties["backend"] = {"enum": ["auto", "cv2", "migan", "lama"]}
            properties["sensitivity"] = {"enum": ["auto", "strict"], "default": "auto"}
            properties["mark"] = text
        required = ("artifact",)
    elif name == "watermark erase":
        properties = {
            "artifact": {"type": "string", "pattern": "^art_[0-9a-f]{32}$"},
            "regions": {
                "type": "array", "minItems": 1,
                "items": {"type": "string", "pattern": "^-?\\d+,-?\\d+,\\d+,\\d+$"},
            },
            "backend": {"enum": ["cv2", "migan", "lama"], "default": "cv2"},
            "inpaint_method": {"enum": ["telea", "ns"], "default": "telea"},
            "dilate": {"type": "integer", "minimum": 0, "maximum": 512, "default": 3},
            "session": text,
            "remote": {"type": "string", "pattern": "^[a-z][a-z0-9-]{0,31}$"},
            "accept_remote": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "max_seconds": {"type": "integer", "minimum": 1, "maximum": 86400},
        }
        required = ("artifact", "regions")
    elif name == "watermark classify":
        properties = {
            "artifact": {"type": "string", "pattern": "^art_[0-9a-f]{32}$"},
            "source": {"type": "boolean", "default": False},
            "remote": {"type": "string", "pattern": "^[a-z][a-z0-9-]{0,31}$"},
            "accept_remote": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "max_seconds": {"type": "integer", "minimum": 1, "maximum": 86400},
        }
        required = ("artifact",)
    elif name == "watermark resample":
        properties = {
            "artifact": {"type": "string", "pattern": "^art_[0-9a-f]{32}$"},
            "model": {"enum": sorted(_superres_models()), "default": "realesrgan-x2plus"},
            "factor": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
            "backend": {"enum": ["auto", "cuda", "openvino", "cpu"], "default": "auto"},
            "tile": {"type": "integer", "minimum": 128, "default": 256},
            "overlap": {"type": "integer", "minimum": 0, "maximum": 128, "default": 16},
            "session": text,
            "budget_approval": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        }
        required = ("artifact",)
    elif name == "watermark batch":
        properties = {
            "directory": text,
            "to": text,
            "mode": {"enum": ["visible", "metadata", "all", "invisible"],
                     "default": "all"},
            "backend": {"enum": ["auto", "cv2", "migan", "lama"], "default": "auto"},
            "sensitivity": {"enum": ["auto", "strict"], "default": "auto"},
            "pipeline": {
                "enum": ["qwen-zimage", "qwen21-zimage", "sdxl-zimage", "chroma-zimage"],
                "default": "sdxl-zimage",
            },
            "force": {"type": "boolean", "default": False},
            "accept_license": {"type": "boolean", "default": False},
            "tile": {"type": "boolean", "default": False},
            "tile_size": {"type": "integer", "minimum": 256, "default": 1024},
            "tile_overlap": {"type": "integer", "minimum": 0, "maximum": 512, "default": 128},
            "cpu_offload": {"type": "boolean", "default": False},
            "max_resolution": {"type": "integer", "minimum": 0},
            "strength": {"type": "number", "minimum": 0, "maximum": 1},
            "seed": {"type": "integer", "minimum": 0},
            "humanize": {"type": "number", "minimum": 0, "maximum": 1},
            "unsharp": {"type": "number", "minimum": 0},
            "adaptive_polish": {"type": "boolean"},
            "text_manifest": text,
            "fidelity_anchor": {"type": "boolean", "default": False},
            "vendor": text,
            "max_seconds": {"type": "integer", "minimum": 1, "maximum": 86400,
                            "default": 3600},
        }
        required = ("directory",)
    elif name == "watermark video identify":
        properties = {"artifact": {"type": "string", "pattern": "^art_[0-9a-f]{32}$"}}
        required = ("artifact",)
    elif name == "watermark video metadata":
        properties = {
            "artifact": {"type": "string", "pattern": "^art_[0-9a-f]{32}$"},
            "remove": {"type": "boolean", "default": False},
            "session": text,
        }
        required = ("artifact",)
    elif name == "watermark video visible":
        properties = {
            "artifact": {"type": "string", "pattern": "^art_[0-9a-f]{32}$"},
            "mark": text,
            "backend": {"enum": ["auto", "cv2", "migan", "lama"], "default": "cv2"},
            "session": text,
            "max_seconds": {"type": "integer", "minimum": 1, "maximum": 86400,
                            "default": 3600},
        }
        required = ("artifact",)
    elif name == "watermark video all":
        properties = {
            "artifact": {"type": "string", "pattern": "^art_[0-9a-f]{32}$"},
            "mark": text,
            "backend": {"enum": ["auto", "cv2", "migan", "lama"], "default": "cv2"},
            "include_invisible": {"type": "boolean", "default": False},
            "noise_std": {"type": "number", "minimum": 0, "maximum": 1},
            "long_side": {"type": "integer", "minimum": 16},
            "fps": {"type": "number", "exclusiveMinimum": 0},
            "batch_size": {"type": "integer", "minimum": 1},
            "seed": {"type": "integer", "minimum": 0},
            "session": text,
            "max_seconds": {"type": "integer", "minimum": 1, "maximum": 86400,
                            "default": 3600},
        }
        required = ("artifact",)
    elif name == "watermark video invisible":
        properties = {
            "artifact": {"type": "string", "pattern": "^art_[0-9a-f]{32}$"},
            "noise_std": {"type": "number", "minimum": 0, "maximum": 1},
            "long_side": {"type": "integer", "minimum": 16},
            "fps": {"type": "number", "exclusiveMinimum": 0},
            "batch_size": {"type": "integer", "minimum": 1},
            "seed": {"type": "integer", "minimum": 0},
            "model": text,
            "device": {"enum": ["auto", "cpu", "cuda"], "default": "auto"},
            "session": text,
            "max_seconds": {"type": "integer", "minimum": 1, "maximum": 86400,
                            "default": 7200},
        }
        required = ("artifact",)
    elif name == "watermark video batch":
        properties = {
            "directory": text,
            "to": text,
            "mode": {"enum": ["all", "visible", "metadata"], "default": "all"},
            "mark": text,
            "backend": {"enum": ["auto", "cv2", "migan", "lama"], "default": "cv2"},
            "include_invisible": {"type": "boolean", "default": False},
            "noise_std": {"type": "number", "minimum": 0, "maximum": 1},
            "long_side": {"type": "integer", "minimum": 16},
            "fps": {"type": "number", "exclusiveMinimum": 0},
            "batch_size": {"type": "integer", "minimum": 1},
            "seed": {"type": "integer", "minimum": 0},
            "max_seconds": {"type": "integer", "minimum": 1, "maximum": 86400,
                            "default": 7200},
        }
        required = ("directory",)
    elif name in {"watermark invisible", "watermark all"}:
        properties = {
            "artifact": {"type": "string", "pattern": "^art_[0-9a-f]{32}$"},
            "session": text,
            "pipeline": {
                "enum": ["qwen-zimage", "qwen21-zimage", "sdxl-zimage", "chroma-zimage"],
                "default": "sdxl-zimage",
            },
            "backend": {"enum": ["auto", "cv2", "migan", "lama"], "default": "auto"},
            "sensitivity": {"enum": ["auto", "strict"], "default": "auto"},
            "tile": {"type": "boolean", "default": False},
            "tile_size": {"type": "integer", "minimum": 256, "default": 1024},
            "tile_overlap": {"type": "integer", "minimum": 0, "maximum": 512, "default": 128},
            "cpu_offload": {"type": "boolean", "default": False},
            "max_resolution": {"type": "integer", "minimum": 0},
            "force": {"type": "boolean", "default": False},
            "accept_license": {"type": "boolean", "default": False},
            "budget_approval": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "memory_mb": {"type": "integer", "minimum": 1},
            "strength": {"type": "number", "minimum": 0, "maximum": 1},
            "seed": {"type": "integer", "minimum": 0},
            "humanize": {"type": "number", "minimum": 0, "maximum": 1},
            "unsharp": {"type": "number", "minimum": 0},
            "controlnet_conditioning_scale": {"type": "number", "minimum": 0},
            "adaptive_polish": {"type": "boolean"},
            "text_manifest": text,
            "fidelity_anchor": {"type": "boolean", "default": False},
            "vendor": text,
            "remote": {"type": "string", "pattern": "^[a-z][a-z0-9-]{0,31}$"},
            "accept_remote": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "max_seconds": {"type": "integer", "minimum": 1, "maximum": 86400, "default": 900},
        }
        required = ("artifact",)
    elif name == "watermark status":
        properties = {}
        required = ()
    elif name == "watermark setup":
        properties = {
            "accept": {"type": "boolean", "default": False},
            "extras": {"type": "string", "pattern": "^[a-z0-9-]+(,[a-z0-9-]+)*$"},
        }
        required = ()
    elif name == "watermark update":
        properties = {
            "accept": {"type": "boolean", "default": False},
            "to": {"type": "string", "pattern": "^[0-9][0-9A-Za-z.+-]{0,31}$"},
            "extras": {"type": "string", "pattern": "^[a-z0-9-]+(,[a-z0-9-]+)*$"},
        }
        required = ()
    elif name == "remote add":
        properties = {
            "name": {"type": "string", "pattern": "^[a-z][a-z0-9-]{0,31}$"},
            "endpoint": {"type": "string", "pattern": "^https?://"},
            "credential_ref": {"type": "string", "pattern": _REF},
            "gateway_credential_ref": {"type": "string", "pattern": _REF},
        }
        required = ("name", "endpoint", "credential_ref")
    elif name in {"remote status", "remote remove"}:
        properties = {"name": {"type": "string", "pattern": "^[a-z][a-z0-9-]{0,31}$"}}
        required = ("name",)
    elif name == "remote list":
        properties = {}
        required = ()
    elif name == "remote jobs list":
        properties = {"provider": {"type": "string", "pattern": "^[a-z][a-z0-9-]{0,31}$"}}
        required = ()
    elif name in {"remote job get", "remote job resume",
                  "remote job retry-interrupted", "remote job cancel"}:
        properties = {"job": {"type": "string", "pattern": "^rmt_[0-9a-f]{32}$"}}
        required = ("job",)
    elif name == "deploy status":
        properties = {
            "vendor": {"enum": ["salad", "modal"]},
            "name": {"type": "string", "pattern": "^[a-z][a-z0-9-]{0,31}$"},
            "salad_key_ref": {"type": "string", "pattern": _REF},
            "worker_token_ref": {"type": "string", "pattern": _REF},
            "salad_org": text,
            "salad_project": text,
        }
        required = ("vendor",)
    elif name == "deploy update":
        properties = {
            "vendor": {"enum": ["salad", "modal"]},
            "image": text,
            "name": {"type": "string", "pattern": "^[a-z][a-z0-9-]{0,31}$"},
            "rename": {"type": "string", "pattern": "^[a-z][a-z0-9-]{0,31}$"},
            "set_env": {
                "type": "array",
                "items": {"type": "string",
                          "pattern": "^[A-Za-z_][A-Za-z0-9_]*=.*$"},
            },
            "salad_key_ref": {"type": "string", "pattern": _REF},
            "worker_token_ref": {"type": "string", "pattern": _REF},
            "modal_token_id_ref": {"type": "string", "pattern": _REF},
            "modal_token_secret_ref": {"type": "string", "pattern": _REF},
            "salad_org": text,
            "salad_project": text,
            "modal_gpu": text,
            "modal_memory_mb": {"type": "integer", "minimum": 256},
            "modal_cpu": {"type": "number", "exclusiveMinimum": 0},
            "accept": {"type": "boolean", "default": False},
            "yes": {"type": "boolean", "default": False},
            "max_seconds": {"type": "integer", "minimum": 1, "maximum": 86400,
                            "default": 900},
        }
        required = ("vendor", "image")
    elif name == "deploy build":
        properties = {
            "tag": {"type": "string", "pattern": "^[0-9][0-9A-Za-z.+-]{0,31}$"},
            "image": text,
            "image_target": {"enum": ["worker", "salad-queue"], "default": "worker"},
            "base_image": text,
            "salad_queue_worker_url_ref": {"type": "string", "pattern": _REF},
            "matting_runtime": {"enum": ["cpu", "gpu"], "default": "cpu"},
            "push": {"type": "boolean", "default": False},
            "registry_user_ref": {"type": "string", "pattern": _REF},
            "registry_password_ref": {"type": "string", "pattern": _REF},
            "accept": {"type": "boolean", "default": False},
        }
        required = ("tag", "image")
    elif name == "deploy remove":
        properties = {
            "vendor": {"enum": ["salad", "modal"]},
            "name": {"type": "string", "pattern": "^[a-z][a-z0-9-]{0,31}$"},
            "salad_key_ref": {"type": "string", "pattern": _REF},
            "salad_org": text,
            "salad_project": text,
            "modal_token_id_ref": {"type": "string", "pattern": _REF},
            "modal_token_secret_ref": {"type": "string", "pattern": _REF},
            "accept": {"type": "boolean", "default": False},
            "yes": {"type": "boolean", "default": False},
        }
        required = ("vendor",)
    elif name == "deploy secret set":
        properties = {
            "name": {"type": "string", "pattern": "^[a-z][a-z0-9-]{0,31}$"},
            "key_file": text,
        }
        required = ("name", "key_file")
    elif name == "compare":
        properties = {
            "first": {"type": "string", "pattern": "^art_[0-9a-f]{32}$"},
            "second": {"type": "string", "pattern": "^art_[0-9a-f]{32}$"},
        }
        required = ("first", "second")
    elif name in {"asset show", "asset delete", "asset remove"}:
        properties = {"artifact": {"type": "string", "pattern": "^art_[0-9a-f]{32}$"}}
        required = ("artifact",)
    elif name == "asset open":
        properties = {
            "artifact_id": {"type": "string", "pattern": "^art_[0-9a-f]{32}$"},
            "folder": {"type": "boolean", "default": False},
        }
        required = ("artifact_id",)
    elif name == "asset save":
        properties = {
            "artifact": {"type": "string", "pattern": "^art_[0-9a-f]{32}$"},
            "path": text,
        }
        required = ("artifact", "path")
    elif name == "asset gc":
        properties = {
            "dry_run": {"type": "boolean", "default": False},
            "plan": text,
            "yes": {"const": True},
        }
    if name in {"providers", "models", "profile list", "session list"}:
        properties.update({key: OPTION_METADATA[key] for key in ("limit", "cursor")})
    schema = _object(properties, required)
    if name == "profile set":
        # Either an existing credential reference, or a key file stored privately now.
        schema["anyOf"] = [{"required": ["credential_ref"]}, {"required": ["key_file"]}]
    return schema


def _data_schema(command: Command) -> dict[str, Any]:
    if not command.available:
        # Only the actual unsupported result is advertised, not hypothetical business output.
        return _object({})
    if command.name == "language":
        return _object(
            {"language": {"type": "string"}, "saved": {"type": "boolean"}},
            ("language", "saved"),
        )
    if command.name == "update":
        return _object(
            {
                "installed": {"type": "string"},
                "latest": {"type": "string"},
                "update_available": {"type": "boolean"},
                "upgraded": {"type": "boolean"},
                "method": {"type": "string"},
                "published_at": {"type": "string"},
            },
            ("installed", "latest", "update_available"),
        )
    if command.name == "profile secret":
        return _object({"profile": {"type": "object"}}, ("profile",))
    if command.name in {"transform", "superres run"}:
        return _object({"artifact": {"type": "object"}, "source": {"type": "string"},
                        "job": {"type": "object"},
                        "artifacts": {"type": "array", "items": {"type": "object"}},
                        "next_actions": {"type": "array", "items": {"type": "object"}},
                        "budget": {"type": "object"}, "error": {"type": "object"}})
    if command.name == "sheet":
        return _object(
            {
                "artifact": {"type": "object"},
                "sources": {"type": "array", "items": {"type": "object"}},
            },
            ("artifact", "sources"),
        )
    if command.name == "superres models":
        return _object(
            {
                "models": {"type": "array", "items": {"type": "object"}},
                "providers": {"type": "array", "items": {"type": "string"}},
                "model_dir": {"type": "string"},
            },
            ("models", "providers", "model_dir"),
        )
    if command.name == "superres install":
        return _object(
            {
                "backend": {"type": "string"},
                "runtime_installed": {"type": "boolean"},
                "runtime": {"type": "object"},
                "installed": {"type": "boolean"},
                "hint": {"type": "string"},
                "models": {"type": "array", "items": {"type": "object"}},
            },
            ("backend", "runtime_installed", "installed", "models"),
        )
    if command.name == "superres fetch":
        return _object(
            {
                "model": {"type": "string"},
                "path": {"type": "string"},
                "bytes": {"type": "integer"},
                "sha256": {"type": "string"},
            },
            ("model", "path", "bytes", "sha256"),
        )
    if command.name == "superres benchmark":
        return _object(
            {
                "artifact": {"type": "string"},
                "reference": {"type": "array"},
                "results": {"type": "array", "items": {"type": "object"}},
            },
            ("artifact", "reference", "results"),
        )
    if command.name == "ops":
        return _object(
            {
                "operations": {"type": "array", "items": {"type": "string"}},
                "presets": {"type": "array", "items": {"type": "string"}},
                "details": {"type": "object"},
            },
            ("operations",),
        )
    if command.name == "compare":
        return _object(
            {
                "first": {"type": "string"},
                "second": {"type": "string"},
                "width": {"type": "integer"},
                "height": {"type": "integer"},
                "identical": {"type": "boolean"},
                "psnr": {"type": ["number", "null"]},
                "ssim": {"type": "number"},
                "ssim_metric": {"type": "string"},
            },
            ("first", "second", "identical", "psnr", "ssim", "ssim_metric"),
        )
    if command.name.startswith("models "):
        return _object(
            {
                "models": {"type": "array", "items": {"type": "object"}},
                "model": {"type": "object"},
            }
        )
    if command.name == "skills list":
        return _object({"skills": {"type": "array", "items": {"type": "object"}}}, ("skills",))
    if command.name == "skills uninstall":
        return _object(
            {
                "scope": {"type": "string"},
                "removed": {"type": "array", "items": {"type": "object"}},
                "conflicts": {"type": "array", "items": {"type": "object"}},
                "failed": {"type": "array", "items": {"type": "object"}},
            },
            ("scope", "removed", "conflicts"),
        )
    if command.name == "skills update":
        return _object(
            {
                "scope": {"type": "string"},
                "updated": {"type": "array", "items": {"type": "string"}},
                "current": {"type": "array", "items": {"type": "string"}},
                "conflicts": {"type": "array", "items": {"type": "object"}},
                "installed": {"type": "array", "items": {"type": "object"}},
                "failed": {"type": "array", "items": {"type": "object"}},
            },
            ("scope", "updated", "current", "conflicts"),
        )
    if command.name == "skills install":
        return _object(
            {
                "scope": {"type": "string"},
                "destination": {"type": "string"},
                "installed": {"type": "array", "items": {"type": "object"}},
                "existing": {"type": "array", "items": {"type": "object"}},
                "available": {"type": "array", "items": {"type": "string"}},
                "targets": {"type": "array", "items": {"type": "string"}},
                "unsupported": {"type": "array", "items": {"type": "string"}},
                "failed": {"type": "array", "items": {"type": "object"}},
            },
            ("scope", "destination", "installed", "existing"),
        )
    if command.name == "cache audit":
        return _object(
            {
                "entries": {"type": "array", "items": {"type": "object"}},
                "plan_digest": {"type": "string"},
                "planned_bytes": {"type": "integer"},
                "count": {"type": "integer"},
            },
            ("entries", "plan_digest", "planned_bytes", "count"),
        )
    if command.name == "cache purge":
        return _object(
            {
                "dry_run": {"type": "boolean"},
                "entries": {"type": "array", "items": {"type": "object"}},
                "plan_digest": {"type": "string"},
                "planned_bytes": {"type": "integer"},
                "deleted_bytes": {"type": "integer"},
                "results": {"type": "array", "items": {"type": "object"}},
            },
            ("dry_run",),
        )
    if command.name == "plan execute":
        return _object(
            {
                "job": {"type": "object"},
                "turn": {"type": "object"},
                "artifacts": {"type": "array", "items": {"type": "object"}},
            },
            ("job", "turn", "artifacts"),
        )
    if command.name in {"generate", "edit"}:
        return _object(
            {
                "plan": {"type": "object"},
                "job": {"type": "object"},
                "turn": {"type": "object"},
            }
        )
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
    if command.name == "plan retry":
        return _object(
            {
                "plan": {"type": "object"},
                "job": {"type": "object"},
                "next_actions": {"type": "array", "items": {"type": "object"}},
            }
        )
    if command.name.startswith("profile "):
        if command.name == "profile list":
            return _object(
                {
                    "profiles": {"type": "array", "items": {"type": "object"}},
                    "next_cursor": {"type": ["string", "null"]},
                },
                ("profiles", "next_cursor"),
            )
        return _object({"profile": {"type": "object"}}, ("profile",))
    if command.name in {"providers", "models"}:
        return _object(
            {
                "items": {"type": "array", "items": {"type": "object"}},
                "next_cursor": {"type": ["string", "null"]},
                "evidence": {"const": "offline_persisted"},
            },
            ("items", "next_cursor", "evidence"),
        )
    if command.name == "session fork":
        return _object({"session": {"type": "object"}}, ("session",))
    if command.name == "session export":
        return _object(
            {
                "archive": {"type": "string"},
                "session_id": {"type": "string"},
                "turns": {"type": "integer"},
                "artifacts": {"type": "integer"},
                "references": {"type": "integer"},
                "omissions": {"type": "array", "items": {"type": "object"}},
            },
            ("archive", "session_id", "turns", "artifacts", "references", "omissions"),
        )
    if command.name == "session import":
        return _object(
            {
                "session": {"type": "object"},
                "turns": {"type": "integer"},
                "artifacts": {"type": "integer"},
                "references": {"type": "integer"},
                "origin_map": {"type": "object"},
                "omissions": {"type": "array", "items": {"type": "object"}},
                "native_contexts_missing": {"type": "array", "items": {"type": "object"}},
            },
            (
                "session",
                "turns",
                "artifacts",
                "references",
                "origin_map",
                "omissions",
                "native_contexts_missing",
            ),
        )
    if command.name == "session delete":
        return _object(
            {
                "session": {"type": "object"},
                "deleted": {"type": "boolean"},
                "exported_files_untouched": {"type": "boolean"},
            },
            ("session", "deleted", "exported_files_untouched"),
        )
    if command.name in {"session remove-artifact", "session remove-image"}:
        return _object(
            {
                "session_id": {"type": "string"},
                "artifact_id": {"type": "string"},
                "removed": {"type": "boolean"},
                "purged": {"type": "boolean"},
            },
            ("session_id", "artifact_id", "removed"),
        )
    if command.name == "asset open":
        return _object(
            {
                "artifact": {"type": "string"},
                "path": {"type": "string"},
                "opened": {"enum": ["image", "folder"]},
            },
            ("artifact", "path", "opened"),
        )
    if command.name == "asset show":
        return _object(
            {
                "artifact": {"type": "object"},
                "path": {"type": ["string", "null"]},
                "available": {"type": "boolean"},
                "owners": {"type": "array", "items": {"type": "object"}},
                "producer_turn": {"type": ["object", "null"]},
                "producer_job": {"type": ["object", "null"]},
                "next_actions": {"type": "array", "items": {"type": "object"}},
            },
            ("artifact", "path", "available", "owners"),
        )
    if command.name == "asset gc":
        return _object(
            {
                "dry_run": {"type": "boolean"},
                "plan": {"type": "string"},
                "candidates": {"type": "array", "items": {"type": "object"}},
                "deleted": {"type": "array", "items": {"type": "string"}},
            },
            ("dry_run", "plan", "candidates", "deleted"),
        )
    if command.name in {"asset delete", "asset remove"}:
        return _object(
            {
                "artifact_id": {"type": "string"},
                "deleted": {"type": "boolean"},
            },
            ("artifact_id", "deleted"),
        )
    if command.name == "asset save":
        return _object(
            {
                "artifact": {"type": "string"},
                "path": {"type": "string"},
                "bytes": {"type": "integer"},
                "sha256": {"type": "string"},
            },
            ("artifact", "path", "bytes", "sha256"),
        )
    if command.name == "session import-image":
        return _object(
            {
                "session": {"type": "string"},
                "artifact": {"type": "object"},
                "path": {"type": "string"},
            },
            ("session", "artifact", "path"),
        )
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
            properties.update({"turns": rows, "references": rows, "links": rows, "artifacts": rows})
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
    if command.name.startswith("deploy "):
        return _object({}, ())
    if command.name.startswith("remote "):
        row = {"type": "object"}
        if command.name == "remote list":
            return _object({"remotes": {"type": "array", "items": row}}, ("remotes",))
        if command.name == "remote remove":
            return _object({"removed": {"type": "string"}}, ("removed",))
        if command.name == "remote jobs list":
            return _object({"jobs": {"type": "array", "items": row}}, ("jobs",))
        if command.name == "remote job get":
            return _object(
                {"job": row, "live": {"type": ["object", "null"]}}, ("job", "live"),
            )
        if command.name == "remote job resume":
            return _object(
                {"job": row, "live": row, "artifact": row, "report": row, "error": row},
                ("job",),
            )
        if command.name == "remote job retry-interrupted":
            return _object({"job": row, "live": row}, ("job", "live"))
        if command.name == "remote job cancel":
            return _object({"job": row}, ("job",))
        # remote add / remote status: the description plus the probed metadata.
        return _object(
            {
                "name": {"type": ["string", "null"]},
                "endpoint": {"type": "string"},
                **dict.fromkeys(
                    ("state_mode", "gpu", "worker_digest", "raiw_version",
                     "imagen_cli_version"),
                    {"type": ["string", "null"]},
                ),
                "meta": {"type": "object"},
            },
            ("name", "endpoint", "meta"),
        )
    if command.name == "watermark status":
        return _object(
            {
                "interpreter": {"type": ["string", "null"]},
                "environment": {"type": "string"},
                "supported_range": {"type": "string"},
                "known_good": {"type": "string"},
                "memory_limit": {"type": ["integer", "null"]},
                "memory_mb": {"type": ["integer", "null"]},
                "ready": {"type": "boolean"},
                "missing": {"type": "array", "items": {"type": "string"}},
                "status": {"type": "string"},
                "error": {"type": "string"},
                "raiw_version": {"type": ["string", "null"]},
                "capabilities": {"type": "object"},
                "cuda": {"type": "boolean"},
            },
            ("interpreter", "environment", "supported_range", "known_good",
             "memory_limit", "memory_mb", "ready"),
        )
    if command.name in {"watermark setup", "watermark update"}:
        return _object(
            {
                "environment": {"type": "string"},
                "target": {"type": "string"},
                "packages": {"type": "array", "items": {"type": "string"}},
                "extras": {"type": "string"},
                "installed": {"type": "boolean"},
                "hint": {"type": "string"},
                "python": {"type": "string"},
                "raiw_version": {"type": ["string", "null"]},
                "capabilities": {"type": "object"},
            },
            ("environment", "target", "packages", "installed"),
        )
    if command.name in {"watermark batch", "watermark video batch"}:
        return _object(
            {
                "source": {"type": "string"},
                "output": {"type": "string"},
                "report": {"type": "object"},
            },
            ("source", "output", "report"),
        )
    if command.name == "watermark video identify":
        return _object(
            {"report": {"type": "object"},
             "raiw_version": {"type": ["string", "null"]}},
            ("report",),
        )
    if command.name in {"watermark video metadata", "watermark video visible",
                        "watermark video all", "watermark video invisible"}:
        return _object(
            {
                "artifact": {"type": "object"},
                "source": {"type": "string"},
                "status": {"type": "string"},
                "report": {"type": "object"},
                "raiw_version": {"type": ["string", "null"]},
            },
            # metadata without --remove and a no-match visible run publish nothing.
            ("artifact",) if command.name in {
                "watermark video all", "watermark video invisible",
            } else (),
        )
    if command.name in {"watermark identify", "watermark classify"}:
        return _object(
            {
                "report": {"type": "object"},
                "raiw_version": {"type": ["string", "null"]},
                "remote": {"type": "object"},
                "job_digest": {"type": "string"},
                "remote_job": {"type": "string"},
            },
            ("report",),
        )
    if command.name == "watermark resample":
        return _object(
            {
                "artifact": {"type": "object"},
                "source": {"type": "string"},
                "report": {"type": "object"},
            },
            ("artifact",),
        )
    if command.name in {"watermark metadata", "watermark visible", "watermark erase",
                        "watermark invisible", "watermark all"}:
        return _object(
            {
                "artifact": {"type": "object"},
                "source": {"type": "string"},
                "status": {"type": "string"},
                "report": {"type": "object"},
                "raiw_version": {"type": ["string", "null"]},
                "remote": {"type": "object"},
                "job_digest": {"type": "string"},
                "remote_job": {"type": "string"},
            },
            # Without --remove, metadata returns the identity report and publishes nothing.
            () if command.name == "watermark metadata" else ("artifact",),
        )
    if command.name == "superres doctor":
        return _object(
            {
                "providers": {"type": "array", "items": {"type": "string"}},
                "requested_backend": {"type": "string"},
                "backend": {"type": ["string", "null"]},
                "provider_used": {"type": ["string", "null"]},
                "fallback": {"type": "boolean"},
                "runtime": {"type": "object"},
                "model": {"type": ["string", "null"]},
                "weights_verified": {"type": "boolean"},
                "inference": {"type": "boolean"},
                "latency_ms": {"type": ["integer", "number", "null"]},
                "output_sha256": {"type": ["string", "null"]},
                "status": {"type": "string"},
                "missing": {"type": "array", "items": {"type": "string"}},
                "hint": {"type": "string"},
            },
            ("providers", "requested_backend", "backend", "status"),
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
        (
            "commands",
            "options",
            "input_schema",
            "data_schema",
            "output_schema",
            "event_schema",
            "limits",
            "manual",
        ),
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
            output_schema["allOf"] = [
                {
                    "if": {"properties": {"ok": {"const": True}}},
                    "then": {"properties": {"data": deepcopy(data_schema)}},
                }
            ]
        record = asdict(command)
        record.pop("summary_en", None)
        record["summary"] = command_summary(command.name)
        records.append(
            {
                **record,
                "input_schema": _input_schema(command.name),
                "data_schema": data_schema,
                "output_schema": output_schema,
            }
        )
    selected = records[0] if name else None
    return deepcopy(
        {
            "commands": records,
            "options": OPTION_METADATA,
            "input_schema": selected["input_schema"] if selected else _input_schema("schema"),
            "data_schema": selected["data_schema"]
            if selected
            else _data_schema(COMMANDS["schema"]),
            "output_schema": selected["output_schema"] if selected else Result.model_json_schema(),
            "event_schema": InvocationEvent.model_json_schema(),
            "limits": {"text_bytes": 1048576, "json_bytes": 4194304, "stdin_seconds": 30},
            "manual": (
                "Input is read from stdin only when explicitly requested; query the schema first. "
                "Undelivered commands return unsupported_capability. input_schema describes "
                "planned structured semantics and does not mean the CLI parameters or provider "
                "support are implemented. data_schema for undelivered commands only describes "
                "the current empty error data. Unknown creation outcomes are not retried "
                "automatically; --yes is not cross-service authorization."
                if not i18n.is_chinese()
                else "仅显式输入才读取stdin；先查询schema。未交付命令返回unsupported_capability。"
                "input_schema描述规划的结构化语义，不代表CLI参数已实现或供应商支持。"
                "未交付命令data_schema只描述当前空错误数据。"
                "unknown不自动重试创建；--yes不是跨服务授权。"
            )
            if agent
            else "",
        }
    )
