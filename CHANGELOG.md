# Changelog

## 0.1.26

### English

- Add an interactive `imagen` launcher with image workspace selection and a unified `imagen skills` manager. Bare Skills maintenance commands guide scope, agents, skill subsets, and final review; back and cancel paths make no changes.
- Keep explicit options and unattended behavior compatible. Support positional skill names, scope-aware target selection, and consistent install/update/uninstall guidance in CLI, TUI, help, and bundled Skills.
- Protect locally edited managed Skills and unmanaged content; reject unsafe symlink updates. Require explicit interactive confirmation before overriding managed edits.
- Keep invalid TUI form input for correction, improve array entry hints, validate shared execution controls, and explain how to install the optional TUI extra.
- Recognize terminated Linux workers even when their parent has not reaped them.

### 中文

- 新增 `imagen` 交互式启动菜单，可选择图片工作区并进入统一的 `imagen skills` 管理。无参数 Skills 维护命令引导选择范围、Agent、技能子集并审阅，支持返回和取消。
- 保留显式选项与无人值守行为。支持位置参数技能名、按范围选择目标，并同步 CLI、TUI、帮助和内置 Skills 的安装、更新及卸载指引。
- 保护已托管技能的本地修改和非托管内容，拒绝不安全的符号链接更新；覆盖修改需明确确认。
- TUI 校验失败保留输入，改善数组输入提示、执行选项校验及可选 TUI 依赖安装指引。
- 修复 Linux 工作进程已结束但尚未回收时仍被误报存活的问题。

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
