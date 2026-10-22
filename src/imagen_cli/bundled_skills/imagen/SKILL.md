---
name: imagen
description: Use when generating or editing images with the local imagen CLI, creating image sessions, planning and approving provider requests, or retrieving generated artifacts. Covers discovery, the plan/approve/execute lifecycle, and the no-auto-retry rules for unknown outcomes.
---

# Imagen 图像工作台

`imagen` 是本机持久图像工作台。会话、计划、批准、产物和失败都保存在本地；CLI 是完整能力面，TUI 只是同一能力的界面。

## 先发现，再调用

- `imagen help --agent`：全部命令的安全调用手册。
- `imagen schema <command>`：单个命令的输入/输出 JSON Schema。
- 机器调用加 `--agent`（等价于 `--output json`）。每次输出一条 envelope：
  `schema_version`、`ok`、`status`、`data`、`error`。
- 不要猜参数；先看 `schema`。位置参数只用于姓名或 ID，例如
  `imagen session create "海报"`、`imagen plan approve pln_... --digest ...`。

## 最小生成流程

```bash
# 1. 建会话（可复用已有会话）
imagen session create "海报草稿" --profile local --agent
# data.session.id / data.session.name

# 2. 规划（不发送供应商请求）；prompt 只能选一个来源
printf '%s' "$PROMPT" | imagen generate \
  --session ses_... --profile local --size 1024x1024 --prompt-stdin --agent
# status=approval_required, data.plan.id, data.plan.plan_digest

# 3. 批准冻结的计划
imagen plan approve pln_... --digest <plan_digest> --agent
# status=approved

# 4. 执行；最多发送一次创建请求
imagen plan execute pln_... --timeout 300 --agent
# status=succeeded, data.artifacts[].id / .path
```

- `--session` 接受会话 ID 或唯一名字。
- `edit` 需要至少一个输入：`--input artifact:art_...` 或 `--input file:path.png`，
  并使用 `--prompt-file` / `--prompt-stdin` 之一。
- 执行成功不一定代表成本已知：`data.job.cost.actual` 可能为 `null`，
  同时给出 `unknown_reason`。永远不要把 `null` 当成 0 或免费。

## 失败与重试

- 停止并报告任何 `status=unknown`（`error.type=outcome_unknown`）或
  `accepted_maybe=true` 的结果。
- 不要自动重发创建请求。先核对本地事实：
  `imagen job get <job_id> --agent`，
  需要补存结果时 `imagen job reconcile <job_id> --agent`，
  再 `imagen job fetch <job_id> --agent`。
- 只有确定没有创建请求时才重试：
  `imagen plan retry <plan_id> --agent` 会重建一个新计划，仍需重新批准。
- `error.retryable` 说明是否值得重试；`error.next_actions` 给出可恢复步骤。

## 常用查询

```bash
imagen session list --agent
imagen session show ses_... --agent        # turns 与 artifact references
imagen job get job_... --agent
imagen job wait job_... --timeout 300 --agent
imagen asset open art_... --agent          # 或 --folder
```

## 安全

- 密钥只写本地私有存储：`imagen profile secret <profile> --key-file <file>`，
  命令不回显密钥。不要在 prompt、日志或提交里复制密钥。
- 未经用户明确同意，不执行 `session delete`、`asset gc`、`cache purge`。
- 需要人机交互时用 `imagen tui`；TUI 的所有能力也能通过 `:` 命令面板到达。
