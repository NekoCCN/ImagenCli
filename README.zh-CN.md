<p align="center">
  <img src="assets/imagen-title-catgirl.png" alt="Imagen 项目图标" width="960">
</p>

<p align="center">
  <strong>连接人、Agent 与图像生成模型。</strong><br>
  <a href="README.md">English</a> · 中文
</p>

Imagen 为人和 coding agent 提供同一个图像工作区。人可以在 TUI 中浏览图片、调整参数、审阅请求、管理产物；agent 可以通过 CLI、JSON 输出和随包 skills，在同一份会话中完成同样的工作。

连接自己的图像端点，创建会话，然后从任一界面继续。计划、执行记录、任务和图片产物都保存在本机，agent 发起的工作可以由人接续查看和处理，反过来也一样。

## 功能

- **同一工作区，两种界面**：TUI 与 CLI 读写相同的会话、计划、任务和产物。
- **自己的端点与模型**：profile 支持 OpenAI-compatible Images 接口及其他适配器，可用模型和参数由端点决定。
- **显式执行**：计划冻结请求供审阅；批准与执行是两步，创建计划不会发送生成请求。
- **本地后处理**：平滑、线条处理、明暗与颜色调整、周期频率抑制、缩放、图像比较和联系表。
- **批量与后台任务**：本地队列、独立子任务和 async worker。
- **Agent skills**：随包指南教 agent 使用 CLI、批准流程、产物管理，以及费用、凭据和权重下载边界；可安装到支持 agent 的项目或全局目录。
- **可选本地超分**：用户自行选择并下载权重，再按固定 SHA-256 摘要校验。
- **中英文界面**：默认英文；JSON 键、状态名和命令标识不随语言变化。

处理始终生成新产物，不覆盖输入。

## 工作方式

```text
请求 ──► 计划（审阅） ──► 批准 ──► 执行 ──► 任务 + 产物
                                              │
                                    后处理 ◄───┴──► 比较 / 联系表
```

一次 turn 记录一次创建尝试；计划记录冻结的请求与摘要；产物记录图片及其来源。会话把 turn 与产物组织在一起。这些都不依赖 TUI：CLI 产生同样的记录，TUI 只是把它们渲染出来。

## TUI 与 CLI 对照

大多数日常操作在两种界面都可用。下表中的按键是 TUI 快捷键。

| 任务 | TUI | CLI |
| --- | --- | --- |
| 浏览会话与图片 | 会话列表、预览区、`[` / `]` | `session list`、`session show`、`asset show` |
| 生成图片 | `g` | `generate --input-json request.json` |
| 编辑图片 | `m` | `edit --input-json request.json` |
| 批准、执行、重试计划 | 计划页：`a`、`enter`、`r` | `plan approve`、`plan execute`、`plan retry` |
| 查看任务 | 不支持 | `job get`、`job wait`、`job fetch`、`job reconcile` |
| 本地后处理 | `t`，`p` 调参 | `ops`、`transform`、`compare`、`sheet` |
| 打开图片或目录 | `o` / `O` | `asset open [--folder]` |
| 分支、导出、删除会话 | `f`、`e`、`x` | `session fork`、`session export`、`session delete` |
| 删除图片 | `d` | `session remove-artifact`、`asset delete` |
| 端点与模型 | `v` | `profile ...`、`models ...`、`providers` |
| 缓存审计与回收 | `c` | `cache audit`、`cache purge` |
| 批量请求 | 不支持 | `batch submit`、`batch get`、`batch wait` |
| 后台 worker | 不支持 | `worker start`、`worker status`、`worker recover` |
| Agent skills | 不支持 | `skills list`、`skills install`、`skills uninstall` |
| 超分模型 | 不支持 | `superres models`、`superres fetch`、`superres benchmark` |
| 命令参考 | `:` 命令面板、`?` 帮助 | `imagen help`、`imagen schema` |
| 切换界面语言 | `l`（会记住） | `--zh` / `--en`、`language` |

## 界面

### TUI

<p align="center"><img src="assets/imagen-tui.png" alt="Imagen TUI 工作区" width="960"></p>

已保存的会话、图片预览与产物切换。

### CLI

<p align="center"><img src="assets/imagen-cli.png" alt="Imagen CLI 命令帮助" width="960"></p>

`generate` 的命令帮助。

## 安装

需要 Python 3.12 或更新版本。

在仓库目录使用 [uv](https://docs.astral.sh/uv/)：

```bash
uv sync --extra tui --extra postprocess
uv run imagen tui
```

也可安装到已有虚拟环境：

```bash
python -m pip install '.[tui,postprocess]'
imagen tui
```

`tui` 安装 Textual；`postprocess` 安装本地图像处理依赖。基础 CLI、profile、会话和端点访问不依赖这两个 extra。

常用入口：

```bash
imagen help                 # 命令索引
imagen help generate        # 单个命令的参数
imagen schema generate      # JSON Schema，不联网
imagen --agent help         # 面向机器的调用手册
```

## 快速开始

1. 配置端点与凭据引用。
2. 创建会话。
3. 生成计划，审阅后批准并执行。
4. 查看任务与产物。
5. 按需做后处理或与其他图片比较。

```bash
export IMAGEN_API_KEY='your-api-key'

imagen profile set local \
  --provider openai \
  --family images \
  --base-url http://127.0.0.1:8000/v1 \
  --model-default your-image-model \
  --credential-ref env:IMAGEN_API_KEY

imagen models refresh --profile local
imagen session create sketchbook --output json
```

把返回的会话 ID 写入 `request.json`：

```json
{
  "schema_version": 1,
  "operation": "generate",
  "session": "ses_REPLACE_WITH_RETURNED_ID",
  "profile": "local",
  "prompt": "A concise cel-shaded illustration with a solid background",
  "count": 1
}
```

```bash
imagen generate --input-json request.json --output json
imagen plan approve PLAN_ID --digest PLAN_DIGEST
imagen plan execute PLAN_ID
```

同样的流程可以在 TUI 中完成：按 `g` 填表，按 `a` 批准，再按 `enter` 执行。

## 配置端点

Profile 保存端点、默认模型和凭据引用。实际可用的模型、操作和参数由端点决定。

```bash
imagen profile set local \
  --provider openai \
  --family images \
  --base-url http://127.0.0.1:8000/v1 \
  --model-default your-image-model \
  --credential-ref env:IMAGEN_API_KEY

imagen profile list
imagen profile show local
imagen profile update local --model-default another-model
```

凭据引用：

- `env:NAME` 读取环境变量。
- `keyring:SERVICE` 读取操作系统 keyring。
- `private:NAME` 读取工作区内的 Imagen 私有存储。

把密钥文件导入私有存储且不回显：

```bash
imagen profile secret --profile local --key-file ./key.txt
```

不要把凭据写进命令参数、请求文件或会话导出。

模型按 profile 声明：

```bash
imagen models refresh --profile local   # 从端点拉取
imagen models add --profile local --id model-id
imagen models list --profile local
imagen models use local --id model-id   # 设为 profile 默认
```

TUI 中按 `v` 可以新增或更新 profile、刷新模型、选择默认模型或手动输入模型 ID。

## 生成与编辑

两种操作走同一套流程。`generate` 创建图片；`edit` 读取已有产物作为输入，也可以带 mask。

```bash
imagen generate --input-json request.json --output json
imagen edit --input-json edit-request.json --output json
```

结果是带 ID 和摘要的计划。计划不会联系供应商。

```bash
imagen plan show PLAN_ID
imagen plan approve PLAN_ID --digest PLAN_DIGEST
imagen plan execute PLAN_ID
```

需要遵守的规则：

- 一次执行最多发送一次创建请求。
- 批准会在执行时消耗，不能重复使用。
- 结果未知时不会自动重试，先核对任务。
- 费用未知时不会记为零。
- `plan execute` 可能产生供应商费用。
- 失败的请求可以用 `plan retry` 重建；它生成新计划，不会复用旧批准。

异步执行使用本地 worker：

```bash
imagen generate --input-json request.json --async
imagen worker start
imagen worker status
imagen worker recover
```

`--wait` 会等待完成，而不是入队后立即返回。

TUI 中 `g` 生成、`m` 编辑。计划页支持批准（`a`）、执行（`enter`）和重试（`r`），批准与执行始终是两个按键。

## 会话、轮次与产物

会话组织 turn 与产物。一个 turn 是一次尝试；产物是带来源信息的已保存图片。

```bash
imagen session create sketchbook --output json
imagen session list
imagen session show ses_...
imagen session resume ses_...
imagen session fork ses_... --from-turn trn_... --name variant
imagen session export ses_... --to backup.zip
imagen session import backup.zip
imagen session delete ses_... --yes
imagen session remove-artifact ses_... art_...
```

产物可以查看、打开或删除：

```bash
imagen asset show art_...
imagen asset open art_...
imagen asset open art_... --folder
imagen asset delete art_...
imagen session remove-artifact ses_... art_...
```

无人引用的受管文件可以回收：

```bash
imagen asset gc                 # dry-run，列出未引用产物
imagen asset gc --plan DIGEST --yes
imagen cache audit
imagen cache purge --plan DIGEST --yes
```

`asset delete` 删除产物实体及本地文件；`session remove-artifact` 从会话移除图片，并在没有其他会话引用时默认回收文件。逻辑删除会话会释放名称以便复用；导出与导入会重映射标识，导出不包含私有凭据。

TUI 对应操作：`n` 新建会话，`f` 从当前轮次分支，`e` 导出，`x` 输入名称确认删除，`d` 删除当前图片，`o` / `O` 打开图片或目录，`c` 审计并回收缓存。

## 任务

```bash
imagen job get job_...
imagen job wait job_... --timeout 600
imagen job fetch job_...
imagen job reconcile job_...
imagen job cancel job_...
```

- `job get` 读取持久任务事实。
- `job wait` 在有界时间内观察本地状态；不轮询供应商。
- `job fetch` 从持久响应补存内联图片或安全下载结果；不会重新生成。
- `job reconcile` 核对本地 staging 并恢复图片。
- `job cancel` 停止本地观察，并在支持的本地执行场景中请求终止。

## 批量请求

`batch submit` 先离线校验全部子请求，然后批准并执行整批，每个子项创建独立任务。

```bash
imagen batch submit --input-json requests.json --concurrency 2
imagen batch get bat_...
imagen batch wait bat_... --timeout 600
```

`requests.json`：

```json
{
  "concurrency": 2,
  "requests": [
    {
      "schema_version": 1,
      "operation": "generate",
      "session": "ses_...",
      "profile": "local",
      "prompt": "first prompt",
      "size": "1024x1024"
    },
    {
      "schema_version": 1,
      "operation": "generate",
      "session": "ses_...",
      "profile": "local",
      "prompt": "second prompt",
      "size": "1024x1024"
    }
  ]
}
```

子任务独立成功或失败。部分失败时不重跑整批；只对确定失败的子项重新规划。子项结果未知时，先核对该任务再决定。

批量提交会发送真实请求。提交前应取得对整批请求的明确授权。

## 本地后处理

后处理目前标记为 experimental。CLI 与 TUI 使用同一份目录，每项说明都会写清计算方法、可能的变化和损失。

<p align="center"><img src="assets/imagen-tui-transform.png" alt="变换预设浏览" width="960"></p>

<p align="center"><img src="assets/imagen-tui-params.png" alt="参数调节" width="560"></p>

浏览预设并实时预览，调整参数后应用。结果另存为新产物。

```bash
imagen ops                                  # 目录
imagen ops --op guided_smooth               # 单个算子
imagen ops --preset '去AI塑料感' --verbose    # 配方详情
imagen transform ARTIFACT_ID --preset '像素化'
imagen transform ARTIFACT_ID --op 'guided_smooth:radius=4,epsilon=0.001,strength=0.5'
imagen compare art_a art_b
imagen sheet --artifacts art_a,art_b,art_c --columns 3
```

目录覆盖平滑、线条提取、明暗与颜色调整、周期频率抑制、缩放、联系表和图像比较。部分操作参考引导滤波、加权最小二乘和 Local Laplacian 方法，近似实现与变体会注明。

限制：

- 平滑可能损伤细线。
- 锐化可能增加噪声。
- 频率抑制可能删除有意的网点。
- 差异指标不能替代视觉检查。
- 尚未完成插画、摄影等领域的图像质量与偏好评价。

TUI：`t` 打开变换工作室，`p` 调参，`enter` 应用。应用会生成新产物，源文件保持不变。

## 可选超分

查看本地模型与许可，不下载权重：

<img src="assets/imagen-cli-superres.png" alt="超分模型与许可" width="760">

```bash
imagen superres models
```

选定模型并查阅来源与权重许可后：

```bash
imagen superres fetch --model realesrgan-x4plus --accept
imagen superres benchmark --artifact ARTIFACT_ID --model realesrgan-x4plus
```

下载会先进入临时目录，按固定 SHA-256 摘要校验，再发布到权重目录。失败或摘要不符不会留下可用权重。

Imagen 不会静默安装超分运行依赖或权重。源码许可不等于模型权重许可。模型重建的纹理不保证存在于原图中。

## Agent skills

随包 skills 教 agent 何时使用 Imagen、如何发现命令与 schema、如何遵守计划与批准流程、如何处理 unknown outcome，以及如何守住费用、凭据和权重下载边界。

```bash
imagen skills list
imagen skills install                     # 在终端里询问装到哪些 agent
imagen skills install --target universal  # 共享的 .agents/skills 目录
imagen skills install --target claude-code --target codex
imagen skills install --scope global --target claude-code
imagen skills uninstall --target codex
```

<img src="assets/imagen-cli-skills.png" alt="Agent skill 安装选项" width="760">

- 默认安装到当前项目；`--scope global` 安装到用户目录。
- 未指定 `--target` 时安装到 `universal`，即多数 agent 都会读取的共享目录 `.agents/skills`。需要更多 agent 时重复传 `--target`；`--target all` 选择兼容表中的全部目标。
- 在终端中未指定 `--target` 时打开勾选列表：方向键移动、空格选择、`a` 全选、回车确认、`q` 取消。共享目录和项目里已存在的 agent 目录默认勾选。没有终端（或加 `--no-interactive`）时直接装到默认目标，不提问。
- 全局安装时会报告仅支持项目作用域的目标。
- 安装会写入托管清单（`.imagen-install.json`）。卸载不会删除未托管目录或符号链接路径；已修改的受管 skill 需要 `--force --yes`。

随包 skills 为 `imagen`、`imagen-assets` 和 `imagen-batch`。

## 机器接口

`--agent` 返回单个 JSON envelope；`--output json` 相同；`--output jsonl` 输出事件流。

```json
{
  "schema_version": 1,
  "ok": false,
  "status": "approval_required",
  "data": {},
  "error": {
    "type": "approval_required",
    "message": "...",
    "retryable": false,
    "accepted_maybe": false,
    "next_actions": []
  }
}
```

中英文下 JSON 键、状态名和标识完全一致，只有人类可读的消息文本跟随界面语言。

退出码：

| 码 | 含义 |
| --- | --- |
| 0 | succeeded、accepted、approved 或 planned |
| 2 | 参数错误或不支持的能力 |
| 3 | 认证或权限错误 |
| 4 | 未找到 |
| 5 | 冲突 |
| 6 | 等待超时、结果未知或需要批准 |
| 7 | 失败、保存失败或取消 |
| 8 | 内容安全拒绝 |
| 9 | 部分成功 |

`imagen schema COMMAND` 返回输入、数据与事件 schema；`imagen --agent help` 返回命令索引与调用规则。

## 数据与隐私

会话、计划、任务、受管图片、缓存和私有凭据都保存在工作区中，默认位于用户数据目录。使用 `--workspace PATH` 可以切换工作区。

- 生成与编辑会把提示词和必要输入发送到配置的端点，适用该端点的保留、日志和计费政策。
- 会话导出不包含私有凭据。
- 供应商错误只保留安全分类；原始响应内容不会出现在人类输出中。
- 结果未知的创建不会自动重试。
- 后处理不会覆盖源产物。
- 未经显式接受，不会下载模型权重。

## 语言

帮助、人类可读输出、错误和 TUI 默认使用英文。

```bash
imagen --zh help                  # 单次命令使用中文
imagen --en help                  # 强制英文
export IMAGEN_LANG=zh-CN          # 仅当前 shell
imagen language zh-CN             # 保存为默认
imagen language                   # 查看已保存的默认
imagen tui
```

`--zh` 和 `--en` 是快捷参数，`--lang en|zh-CN` 是显式写法。语言参数优先于 `IMAGEN_LANG`，同一条命令只能使用一个语言选项。

`imagen language [en|zh-CN]` 会把默认语言写入平台配置目录（Imagen 用户配置路径下的 `config.json`，仅本人可读）。TUI 内按 `l` 同样会保存，重启后保持。优先级依次为：命令行参数、`IMAGEN_LANG`、已保存的默认值、英文。

## 常见问题

**`approval_required`。** 先审阅计划，然后带摘要执行 `plan approve`，再执行 `plan execute`。

**结果为 `unknown`。** 不要重跑请求。先用 `job get` 核对；如果已有持久响应，使用 `job fetch` 或 `job reconcile`。

**模型不存在。** 运行 `models refresh --profile NAME`，或用 `models add --profile NAME --id MODEL` 手动声明。

**端点拒绝编辑或某个参数。** 能力取决于供应商和模型。检查 `providers`、模型目录和端点文档。

**TUI 预览不可用。** 图片预览需要 truecolor 终端；产物仍可用 `o` 或 `imagen asset open` 打开。

**skill 无法卸载。** 目录未托管、是符号链接，或安装后被本地修改。先检查报出的路径；已修改的受管 skill 需要 `--force --yes`，未托管目录不会被接管。

**超分在推理前失败。** 缺少运行依赖或权重。运行 `superres models`，审阅许可后执行 `superres fetch --model NAME --accept`。

## 后续发展

- **端点执行**：在现有本地队列与 worker 之外，补齐远程异步任务处理。
- **图像质量**：扩展插画、摄影等领域的视觉对照与用户偏好评价；完成前，后处理保持 experimental。
- **平台支持**：补充 Windows 实机验证，改进 Windows/macOS 处理资源控制。目前尺寸、缓冲和超时检查不构成完整沙箱；Linux 地址空间限制也不是 RSS 或显存上限。
- **语言支持**：增加更多界面语言。默认英文，中文可通过 `--lang zh-CN` 或 `IMAGEN_LANG=zh-CN` 使用。
- **界面一致性**：把批量、worker、skills 和超分管理带入 TUI。

## 开发

```bash
uv sync --group dev --extra tui --extra postprocess
uv run pytest
uv run ruff check .
uv run mypy src
```

默认测试离线运行。live 供应商测试标记为 `live`，需要显式授权和凭据，不属于日常开发流程。

源码结构：

| 路径 | 职责 |
| --- | --- |
| `src/imagen_cli/cli` | 入口、命令注册、渲染器、TUI |
| `src/imagen_cli/application` | 规划、执行、产物、会话、后处理、skills |
| `src/imagen_cli/domain` | 契约、标识、错误 |
| `src/imagen_cli/providers` | 端点适配器 |
| `src/imagen_cli/execution` | 队列、worker、已配置执行 |
| `src/imagen_cli/storage` | SQLite、产物、私有状态、工作区安全 |

## 参与贡献

欢迎范围明确的修改。行为变化请补充测试，保留端点请求的计划与批准边界；未经明确授权，不新增 live 或付费端点测试。

## 许可证

Imagen 源码采用 [MIT License](LICENSE)。第三方依赖、端点服务和模型权重适用各自的许可与条款。

