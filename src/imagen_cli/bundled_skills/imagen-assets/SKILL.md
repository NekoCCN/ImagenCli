---
name: imagen-assets
version: 1.2.0
description: Use when browsing or resuming Imagen sessions, opening or exporting artifacts, applying local transforms, comparing images, upscaling, or reclaiming workspace disk space.
---

# Imagen sessions and artifacts

Explain choices, confirmations, and results in the user's language. Keep commands, flags, IDs, and JSON keys unchanged. Use CLI help from the installed version before unfamiliar operations.

Artifacts are managed files. Transforms and contact sheets create new artifacts without overwriting inputs; comparison is read-only.

## Session history

```bash
imagen session list --agent
imagen session show ses_... --agent
imagen session resume ses_... --agent
imagen session fork ses_... --from-turn trn_... --name branch --agent
imagen session export ses_... --to backup.zip --agent
imagen session import backup.zip --agent
imagen asset show art_... --agent
imagen asset open art_... --agent
```

Choose the user's session and artifact, not a similar-looking one. Import remaps IDs and resolves duplicate session names. Session deletion is logical and frees its name. Exports omit credentials and private storage. Open the actual output for the user; a successful command alone does not display it.

## Deterministic postprocessing

```bash
imagen ops --agent
imagen transform art_... --op pixelate:scale=4 --agent
imagen transform art_... --pipeline pipeline.json --agent
imagen compare art_a art_b --agent
imagen sheet --artifacts art_a,art_b,art_c --columns 3 --agent
```

`transform` requires `--op`, `--pipeline`, or `--preset`. `compare` reports PSNR/SSIM without creating an artifact. A contact sheet infers a common source session; use `--session` for inputs from different sessions. Postprocessing is experimental: metric changes do not establish better perceptual quality.

## Model restoration and runtime installation

`superres` also runs same-size restoration: `film-degrainer` denoises and `dejpg-srformer-light` reduces JPEG artifacts. Neither enlarges the image. Inspect the catalog task, source, and license rather than inferring capabilities from the command name.

Preview runtime installation with `imagen runtime install --backend cpu --agent`. After the user authorizes installing dependencies, add `--accept`. Available installation backends are `cpu`, `cuda`, and `openvino`. The command targets the current virtual environment, refuses system Python and conflicting ONNX packages, and does not install GPU drivers. Restart Imagen after installation. Dependency installation does not authorize weight downloads or paid provider calls.

Inspect the catalog and let the user select a model and review its source/license. Only download after explicit approval; this example does not select a model for the user:

```bash
imagen superres models --agent
imagen superres fetch --model realesrgan-x4plus --accept --agent
imagen transform art_... --op superres:backend=cpu,tile=256,overlap=16 --agent
```

Do not download weights or install inference dependencies automatically. Report permission/dependency errors. Never substitute ordinary resizing and claim successful model-based upscaling.

## Reclaim disk space

```bash
imagen cache audit --agent
imagen cache purge --plan DIGEST --yes --agent
imagen asset gc --dry-run --agent
imagen asset gc --plan DIGEST --yes --agent
```

Present the candidate list and obtain approval before deleting. Use the digest returned by the current dry run; never invent it. Exported ZIPs are unaffected. Artifacts referenced by undeleted sessions are not garbage-collected. Never broaden cleanup to unrelated files.
