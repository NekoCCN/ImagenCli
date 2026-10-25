# Product

<!-- impeccable:product-schema 1 -->

## Platform

terminal

## Users

个人本地工作台：一个人在自己的机器上，通过本机或局域网的 OpenAI 兼容网关生成角色图、海报等素材。人类与机器并重：人类在 TUI 里浏览、选择与确认，脚本或 agent 用 CLI 的 `--agent` / `--output json` 驱动同一套能力。

## Product Purpose

把一次图片创作从“一次请求”变成可恢复的工作。会话、计划、批准、产物、失败与重试都持久化在本地；成功意味着关掉终端后还能接着做，脚本也能可靠地重复调用。

## Positioning

三者合一：本地持久工作台、任意 OpenAI 兼容网关与模型、生成后的确定性后处理（变换、超分、对比、联系表）。相邻工具通常只做其中一条；这里三条共享同一份会话、产物与批准记录。

## Operating Context

- 端点通常是本机或局域网网关，不需要公网；模型列表从端点目录刷新后挑选，手输模型 ID 也保留。
- 中文界面与中文帮助；默认输出面向人类，机器输出走显式参数。
- 计划、批准、执行分离：执行最多发送一次创建请求；已消费的批准不复用，重试会重建请求并生成新计划。
- 已知失败允许重试；结果未知时不自动重试，也不把成本记成 0。

## Capabilities and Constraints

- CLI 是完整能力面；TUI 必须覆盖同一组能力，至少通过 `:` 命令面板覆盖全部可用命令，常用路径有直达键。
- 本地优先：SQLite 状态与文件产物都在本机；密钥只写入本地私有存储，不回显，不进入导出。
- 已移除能力证据目录：端点与模型由用户选择后直接规划，不由本地能力表把关。
- 已交付：generate/edit、会话分支/导出/导入/名称释放、会话内单张图片安全删除（TUI `d`/`Delete` + CLI `session remove-artifact`/`remove-image`）、模型目录刷新与选择、批量、异步 worker 与队列消费、缓存与 GC、确定性变换（全套去AI/ACG特化/摄影光影算子共25+种）、全流程预设（`--preset` CLI/TUI双向对齐）、算子与预设学术原理知识库（`ops` 详尽输出 + TUI 实时说明）、超分/对比/联系表、`asset show`/`asset open`、失败原因显示。
- 终端平台：Textual TUI 为可选依赖，80×24 下主要界面可用；键盘操作为主。

## Brand Commitments

名称 Imagen；界面与帮助以中文为主。

## Evidence on Hand

- 2026-10-24 本地网关真实闭环：approve → execute 22.4 秒成功，产物 1254×1254 保存到受管目录；异步入队 → worker 执行 → 1024×1024 产物同样成功。
- 失败原因可见：job 记录类型、HTTP 状态与异常类，TUI 与 CLI 都显示中文说明；unknown 不自动重试。
- 超分多模型自洽基准（SwinIR-S / Real-ESRGAN）与真实 ONNX 推理可用。
- 高级后处理算子与知识库对齐：去AI感（电影级色调、胶片微噪点、消色差微影调、高斯扩散光晕、引导滤波保边）、ACG特化（自适应线稿染色、日漫扩散柔光、大魄力鱼眼、热血集中线、黑白网点、立绘贴纸、桑原平涂）、三段色彩平衡与色彩迁移全量通过确定性与可重复性单测；CLI `transform --preset` 与 `ops` 知识库和 TUI 严格对齐。
- 会话内图片删除：TUI 快捷键 `d` / `Delete` 二次确认与 CLI `session remove-artifact`（支持别名 `remove-image` 与 `--purge`）全链路验证通过，孤儿产物物理安全回收与共享产物防误删严格隔离。
- 2026-10-24 当时门禁：pytest 920+ passed / 2 skipped，ruff 与 mypy（95 文件）通过；generated_docs --check 零漂移。
- 限制：Windows 实机未验证；远程异步类 provider 的执行路径未完成。

## 后处理升级实施状态（2026-10-25）

以下是已实现能力，不代表完整设计或图像质量验收完成。

- 新增 20 个实验性操作：`local_laplacian_sampled`、`weighted_guided_smooth`、`wls_detail`、`periodic_notch_auto`、`fast_guided_smooth`、`weighted_median`、`guided_smooth`、`rolling_guidance`、`flow_line`、`kuwahara_anisotropic`、`l0_smooth`、`domain_transform`、`wls_smooth`、`rtv_smooth`、`bounded_sharpen`、`multiscale_detail`、`periodic_notch`、`chroma_transfer`、`linear_resize`、`illumination_correct`。RTV、方向 Kuwahara、线条积分、多尺度和照明操作的变体或近似身份写入执行记录，不宣称完整复现原论文。
- CLI 与 TUI 共用配方目录，保留基础配方和历史入口。新算法说明标记实验状态，采用计算方式、适用条件和副作用的陈述句。裸 `kuwahara` 固定使用 NumPy legacy，缺少 NumPy 时明确报错；两个历史实现另有显式 ID。
- 输入 ICC 转为 sRGB，参考配色图也做颜色管理；损坏 ICC、未标记 CMYK/LAB 和高位深输入拒绝，不静默降位深。输出嵌入 sRGB ICC。结构求解器以可见像素的覆盖率加权均值延拓零 Alpha 区域，并恢复隐藏 RGB；这不是完整 masked-domain 求解。
- 新产物记录完整默认参数、算法实现、版本、边界、工作空间、Alpha 策略、近似身份和 Pillow/NumPy/LittleCMS 版本；参考配色保存参考文件哈希。原图不覆盖。
- 增加输入 32 MP、输出 64 MP 和估计工作缓冲 2 GiB 的预检。尺寸跟踪覆盖 resize、crop、pad、perspective、rotation/transpose、block_scale、scale2x 和已登记超分倍率。缓冲估计不是实测峰值。后处理在 spawn 子进程计算，成功后父进程才发布资产；默认整条管线含启动时间 120 秒超时，CLI 可用 `--timeout` 整数秒调整，application 调用支持取消 Event。超时/取消终止并清理临时结果，Linux 限制额外虚拟地址空间为 2 GiB；该限制不是 RSS 或设备显存上限，Windows/macOS 暂无同等 OS 内存限制，TUI 的默认处理路径异步等待文件计算，SQLite 发布保留在 UI 线程，Esc 可取消在途处理；注入的自定义 transform 回调沿用原同步调用契约。
- 本机 192×128 RGBA 单图验证：引导平滑 0.022 s、流向线条 0.535 s、优化后方向 Kuwahara 1.170 s。仅证明该尺寸可运行，不外推大图性能或图像质量。
- 最新离线门禁（2026-10-25）：1324 passed、5 skipped；68 条既有 release_readiness 元数据弃用警告。ruff、mypy（102 文件）、契约文档检查、`uv lock --check` 和 git diff --check 通过。未执行发布、权重下载或付费生成。
- CLI 实际 JSON 输出与共享目录核对一致：113 个操作、44 个配方。修复历史双语配方名称未匹配分类的问题，80×24 Textual 测试挂载并遍历分类覆盖全部 44 个配方。

- `--op` 支持频率坐标、RGB 三元组和带双引号的逗号字符串，并拒绝重复参数。可选算子注册拒绝重复 ID，模块内部 ImportError 不再静默当成缺依赖。
- 导入保留未知后处理来源对象，不执行任何管线；返回 `postprocess_replay` 兼容性状态，区分未知 schema、历史实现歧义、版本/实现不兼容及外部参考输入缺失。`replayable` 只指当前管线契约匹配，不承诺跨构建字节一致。`--pipeline` 可显式读取 schema 2 来源对象并重放已匹配步骤；模型身份未验证或依赖外部参考字节的记录拒绝重放，不自动读取历史路径。
- 加权中值采用 [100+ Times Faster Weighted Median Filter](https://www.cse.cuhk.edu.hk/leojia/projects/fastwmedian/download/fastweightedmedian_cvpr14.pdf) 式 2 的排序累计定义，实现 RGB Gaussian 相似度与 Alpha 覆盖率加权的参考变体，不冒充论文的 joint histogram / median tracking 加速实现。本机 192×128 RGBA 为 0.088 s；32×32 固定脉冲夹具 MAE 从 9.6875 降为 0，仅说明该合成退化，不外推真实图质量。

- 快速引导变体按 [Fast Guided Filter](https://arxiv.org/html/1505.00996) Algorithm 2 低分辨率估计系数，再应用原分辨率引导；覆盖率加权、半径取整和插值策略明确记录。倍率 1 与完整引导路径逐字节一致。同机单次 768×512 RGB、radius=16：完整路径 0.5409 s，快速路径 0.1284 s，约 4.21 倍；两者平均绝对像素差 0.4861/255，不是恢复误差或画质收益。合成细线负例确认快速变体可能比完整路径更削弱一像素线，保留实验标签。

- `periodic_notch_auto` 使用亮度平面去趋势、Hann 加窗和局部频谱峰对比，记录被抑制的 cycles/pixel 频率；常量、平滑渐变和全透明图无峰旁路。有意条纹负例同样会被处理，检测不是噪声语义判断。`wls_detail` 使用三个固定引导 WLS 基础层及可重建残差，所有增益为 1 精确旁路，区别于 Gaussian 多尺度和 Local Laplacian。
- 模型候选已补查原作者仓库许可与运行链：APISR 的 GPL 与 academic-use-only 并存声明、SUPIR 独立商业许可构成明确授权障碍。其余 checkpoint 格式、权重身份和适配仍待闭合，不将源码许可证等同权重许可，也不将尚未写适配误报为模型不可运行。

- `weighted_guided_smooth` 按 WGIF（2015）式 5–10 引入局部方差边缘权重；亮度引导 RGB、覆盖率全图归一与 sigma=1 权重平滑是明确变体。`local_laplacian_sampled` 按 Local Laplacian（2011）点重映射构造参考亮度采样金字塔并插值系数，不是原逐系数精确算法；Gaussian 降采样、双线性展开与隐藏 RGB 均值延拓均写入说明。两者已覆盖共享参数、CLI/TUI 配方与来源身份，仍为实验性。
- 比较指标改为带 `ssim_metric=local_gaussian_pillow_l_v1` 的局部 Gaussian SSIM，原全局实现保留为 `ssim_global_legacy`。历史指标不改写、不与新值混比；小图窗口、Pillow L、Alpha 与颜色边界见 [指标说明](docs/designs/image-quality-metrics.md)。TUI 页面卸载也会触发在途取消。旧本地 TorchScript 加载禁用，不自动换后端。ONNX 图预检已接入：拒绝 external data、非标准域（含 `ai.onnx.ml`）、未知标准算子、越界 opset 和本地 function，预检后的同一份字节直接交给 ORT；已在真实 `onnx 1.23.2` 与 `onnxruntime 1.23.2` 验证。预检是格式与语义策略检查，不是沙箱或资源上限。模型候选核验记录见 [模型证据](docs/designs/postprocess-model-evidence.md)。

- 离线评测入口 `scripts/eval_postprocess.py` 支持合成负例、显式 PNG 原尺寸输入、干净目标配对退化及 `--ablation` 有界单参数消融，拒绝覆盖已有输出目录。13 个合成变体的 binary/fractional Alpha 检查均通过。细线和赛璐璐色块的固定种子 Gaussian 合成退化配对结果见 `live-results/postprocess-paired-20261025-run3/report.json`；该结果记录干净目标和输入 PNG 的 SHA-256，以及输入和变体相对干净目标的 RGB MAE/局部 SSIM，但仅覆盖两种合成退化。六张已知本地图已完成原尺寸四变体处理，每个变体保存两个同坐标 256×256 裁切，记录见 `live-results/postprocess-six-full-1792908735368181254/report.json`。完整裁切对照表及六张分 case 对照表已生成；60 张裁切和 7 张新对照表已验证可解码、尺寸正确。可靠的视觉记录仍限于此前三个 case 的一个裁切；后续图像读取返回编码数据，不据此宣称全量视觉核对完成。样本存在近重复。相对源图的 MAE/SSIM 不是恢复质量排名，范围与限制见 [评测记录](docs/designs/postprocess-pyramid-weighting.md)。

未完成：完整历史歧义迁移与外部参考/模型重放；跨平台 OS 内存限制；新增学习型恢复与生成式重建的具体权重授权闭合、后端适配与实测；完整多域对照、系统消融、全量原尺寸局部视觉核对和用户偏好验收。目前只有两种合成退化的干净参考目标，真实图没有干净参考目标。因此新算法不标记为 recommended，也不声称完整升级已完成。Kuwahara 性能重构的代理交付先改实现再补等价性测试，未遵守本轮要求的先红 TDD 顺序。

## Product Principles

1. 本地是事实来源：状态、产物与密钥都不依赖云服务。
2. 同一能力只实现一次：CLI 与 TUI 共用服务与数据，不各建一套。
3. 人类默认可读，机器显式结构化：好看的默认输出与稳定的 JSON 不互相妥协。
4. 失败可重试，未知不伪造：已知失败重建请求；未知结果绝不自动重发或记成免费。
5. 不为端点做证据仪式：把精力花在创作、预览与后处理，而不是能力审查流程。

## Accessibility & Inclusion

键盘优先；中文为主；80 列终端是必须成立的最小宽度。
