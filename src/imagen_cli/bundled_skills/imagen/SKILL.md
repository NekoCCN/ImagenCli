---
name: imagen
version: 1.0.0
description: Use when the user wants to generate or edit images, manage image sessions, or process local images using the Imagen CLI. Includes command discovery, structured output, user approval before provider requests, and safe handling of unknown outcomes.
---

# Imagen CLI

用 `imagen` 完成用户的图像任务。生成和编辑使用已配置端点；会话、执行记录和产物保存在本机。不要把本地工作台理解为本地运行生成模型。

## 先发现能力

```bash
imagen help --agent
imagen schema generate
imagen profile list --agent
```

- 使用当前安装版本的 `help` 和 `schema`，不要凭记忆猜参数。
- 机器调用显式加 `--agent`，或 `--output json`；同时检查退出码和 `ok/status/data/error`。
- 缺少 profile 或模型时请用户选择，不读取密钥文件、不输出环境变量或凭据配置。
- 查询或本地处理不需要调用付费生成端点。

## 生成与编辑

1. 确认用户的提示词、输入、profile、模型和数量。说明输入将发送到该端点。
2. 创建或复用会话，调用 `generate` 或 `edit` 生成冻结计划；此时未发送创建请求。
3. 检查计划，将请求范围、目标与可获得的费用信息呈现给用户。`approval_required` 是待批准，不是失败或生成成功。
4. 只有用户明确授权当前请求后，才调用 `plan approve` 与 `plan execute`。不要因为用户想看计划就替他们执行。
5. 从结果读取实际 artifact ID 和路径，不编造文件名。费用为 `null` 代表未知，不代表免费。

完整 JSON 示例和恢复步骤见 [工作流](references/workflows.md)。

## 不确定结果

`unknown`、`outcome_unknown` 或 `accepted_maybe=true` 时停止创建操作，检查 `job get`、`job reconcile` 等本地事实并告知用户。不要自动重新生成，也不要通过新建会话规避未知结果。

`--async` 和 `batch submit` 会直接排队或执行，不是绕过用户授权的方法；必须事先获得对应批次或请求的明确发送授权。

## 其他任务

- 会话、产物、变换、比较、联系表和超分：参见随包的 `imagen-assets` skill；也可直接查询 `imagen help COMMAND`。
- 批量创建与部分失败处理：参见 `imagen-batch` skill。
- 本地后处理为 experimental，会另存产物；不要覆盖原图，不把指标差异说成画质提升证明。
- 查看模型目录不下载权重。用户选定模型并接受来源许可后才能执行 `superres fetch --accept`，不得擅自下载或安装推理依赖。
- 删除、垃圾回收、覆盖或强制卸载先取得用户明确确认。
