# Imagen 工作流

## 创建与执行

先运行 `imagen session create "草稿" --agent`，使用返回的会话 ID 编写 request.json：

```json
{
  "schema_version": 1,
  "operation": "generate",
  "session": "ses_REPLACE",
  "profile": "USER_SELECTED_PROFILE",
  "prompt": "USER_APPROVED_PROMPT",
  "count": 1
}
```

```bash
imagen generate --input-json request.json --agent
```

返回 `approval_required` 时，读取 `data.plan.id` 和 `data.plan.plan_digest`，显示计划并获得用户授权，再执行：

```bash
imagen plan approve PLAN_ID --digest PLAN_DIGEST --agent
imagen plan execute PLAN_ID --agent
```

成功后检查 `data.artifacts` 和任务记录。`null` 费用是未知，不是零。编辑使用 `operation=edit`，输入字段先通过 `imagen schema edit` 查询，不能臆测模型支持编辑。

## 恢复

```bash
imagen job get JOB_ID --agent
imagen job reconcile JOB_ID --agent
imagen job fetch JOB_ID --agent
```

先检查事实再决定是否恢复；这些入口不代表可以重发创建请求。只有确定失败或取消的计划才考虑 `plan retry`，新计划仍需要用户授权。结果未知时让用户决定下一步。

## 本地处理与模型

```bash
imagen ops --agent
imagen superres models --agent
```

后处理为 experimental。平滑可能损伤细线，锐化可能增加噪声，超分纹理不保证存在于原图。不同模型的依赖、许可和可下载性以当前目录为准。

用户选择模型、查阅来源与权重许可并明确同意下载后：

```bash
imagen superres fetch --model realesrgan-x4plus --accept --agent
```

不得默认选择模型或自动附加 `--accept`。缺少推理运行时应报告依赖，不自动安装；下载完成也不意味着能够执行推理。

## 安装本指南

```bash
imagen skills install --target codex --target claude-code --agent
imagen skills list --target codex --agent
imagen skills uninstall --target codex --agent
```

默认 project，使用 `--scope global` 可供跨项目使用。`--target all` 明确选择所有支持目标，可能创建许多工具目录，执行前说明范围。安装冲突或卸载发现本地修改时先报告，不擅自加 `--force --yes`。
