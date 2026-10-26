---
name: imagen-batch
version: 1.0.0
description: Use when submitting several imagen image requests at once, controlling provider concurrency, or inspecting and waiting on a batch of child jobs. Covers batch submit/get/wait, per-child status, and safe handling of partial failures.
---

# Imagen 批量任务

批量把多个独立的创建请求交给同一个并发上限执行。每个子项仍是独立 turn/job，失败互不覆盖。

## 提交

`batch submit` 会批准并执行各子请求，不是只生成待审计划。先向用户确认整批提示词、端点、模型、数量和可能费用，取得明确发送授权后才提交。

准备一个 JSON 文件，`requests` 是 CreationInput 数组，`concurrency` 取 1–8：

```json
{
  "concurrency": 2,
  "requests": [
    {
      "schema_version": 1,
      "operation": "generate",
      "session": "ses_...",
      "profile": "local",
      "prompt": "第一张的提示词",
      "size": "1024x1024"
    },
    {
      "schema_version": 1,
      "operation": "generate",
      "session": "ses_...",
      "profile": "local",
      "prompt": "第二张的提示词",
      "size": "1024x1024"
    }
  ]
}
```

```bash
imagen batch submit --input-json requests.json --agent
# data.batch.id（bat_...）、子任务列表
```

- `--input-json -` 从 stdin 读 JSON。
- 提交前会离线校验全部子请求；任何一个不合法都整体拒绝。
- 并发最终取 `min(请求值, 8, 供应商声明上限)`。

## 查看与等待

```bash
imagen batch get bat_... --agent
imagen batch wait bat_... --timeout 600 --agent
```

- 每个子项有自己的 job 状态与产物；按子项逐个判断成功/失败。
- `batch wait` 只观察，不重试、不重发。
- 部分失败是正常结果：不要自动重新提交整批；只对确定的失败子项重新规划。
- 结果未知（`outcome_unknown` / `accepted_maybe=true`）时停止，先核对对应 job，
  再让用户决定。
