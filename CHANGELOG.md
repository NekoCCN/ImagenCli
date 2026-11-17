# Changelog

## 0.1.25

### English

- Fix timing-sensitive NovelAI TUI tests by waiting for form transitions and callbacks to finish.
- Add regression coverage for delayed Vibe form dispatch. Runtime behavior and API compatibility are unchanged.

### 中文

- 修复 NovelAI TUI 测试的时序问题，等待表单切换和回调完成后再检查结果。
- 补充 Vibe 表单延迟调度的回归测试。运行时行为与接口兼容性未变。

## 0.1.24

## English

- Add NovelAI-compatible text-to-image, single-image img2img, Vibe inspiration, and precise character references to the CLI and TUI.
- Add separate Vibe encoding plans and approvals, with matching encodings kept in a private local cache. Encoding may incur a separate charge.
- Use official JSON Vibe encoding by default; support explicit multipart encoding for documented gateways. Generation remains JSON, with no automatic fallback or retries.
- Update bundled Skills with NovelAI workflows and batch inspection guidance.

Compatibility is covered by offline tests, not live or paid service verification. Inpainting, streaming previews, Director Tools, account/balance management, and automatic pricing are not supported. Unknown outcomes may already have incurred a charge; review before trying again.

Install the wheel or source distribution attached to this release. Python 3.12+ is required.

## 中文

- CLI 与 TUI 新增 NovelAI 兼容文生图、单图 img2img、Vibe 灵感参考和精确角色参考。
- 新增独立的 Vibe 编码计划与批准流程，完全匹配的编码保存在本地私有缓存中。编码可能单独收费。
- Vibe 编码默认使用官方 JSON；可按网关文档显式选择 multipart。生成请求仍为 JSON，不会自动回退或重试。
- 更新随包 Skills，补充 NovelAI 工作流与批量任务检查指引。

兼容性仅经离线测试，未经实际或付费服务验证。不支持局部重绘、流式预览、Director Tools、账号／余额管理和自动报价。结果未知时可能已产生费用，请审阅后再决定是否重新尝试。

可安装本次发布附带的 wheel 或源码包，需要 Python 3.12+。
