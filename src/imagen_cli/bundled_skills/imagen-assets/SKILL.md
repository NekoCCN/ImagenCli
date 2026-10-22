---
name: imagen-assets
description: Use when working with existing imagen sessions and artifacts: listing or resuming history, forking, exporting or importing a session, applying deterministic transforms, comparing, building contact sheets, running super-resolution, or reclaiming disk space.
---

# Imagen 会话与产物

产物是受管文件；变换、对比、联系表都会生成新产物，不覆盖源文件。

## 会话历史

```bash
imagen session list --agent
imagen session show ses_... --agent        # 轮次、引用与产物 ID
imagen session resume ses_... --agent
imagen session fork ses_... --from-turn trn_... --name "分支" --agent
imagen session export ses_... --to backup.zip --agent
imagen session import backup.zip --agent
```

- 导入会重映射全部 ID，同名会话自动加 `(imported)` 后缀。
- `session delete` 是逻辑删除；名字立即释放，可再用同名新建。
- 导出不包含密钥或私有存储。

## 确定性后处理

```bash
imagen ops --agent                        # 可用算子
imagen transform art_... --op pixelate:scale=4 --agent
imagen transform art_... --pipeline pipeline.json --agent
imagen compare art_a art_b --agent        # PSNR / SSIM，只读
imagen sheet --artifacts art_a,art_b,art_c --columns 3 --agent
```

- `transform` 必须给 `--op` 或 `--pipeline` 之一。
- 联系表默认从源产物推断共同会话；源产物跨会话时用 `--session` 指定归属。

## 超分

```bash
imagen superres models --agent
imagen superres fetch realesrgan-x4plus --accept --agent
imagen transform art_... --op superres:backend=cpu,tile=256,overlap=16 --agent
```

权限或依赖缺失是明确错误；不要用普通缩放假装超分结果。

## 回收空间

```bash
imagen cache audit --agent                # 只读
imagen cache purge --plan <digest> --yes --agent
imagen asset gc --dry-run --agent       # 列出候选，不删除
imagen asset gc --plan <digest> --yes --agent
```

- 没有 `--yes` 与匹配的摘要，永远不删除。
- 删除前把清单给用户确认；已导出的 ZIP 不受影响。
- 产物仍在被未删除会话引用时不会被回收。
