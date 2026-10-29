---
name: imagen-assets
version: 1.4.0
description: Use when browsing or resuming Imagen sessions, importing or saving artifacts, applying local transforms, comparing images, upscaling, or reclaiming workspace disk space.
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
imagen session import-image ses_... photo.png --agent
imagen asset show art_... --agent
imagen asset open art_... --agent
imagen asset save art_... ./copy.png --agent
```

`session import-image` copies an external image into the session as a new artifact without changing the source file, so transforms, edits, and model runs can use it. `asset save` writes a verified copy to a new path and refuses to replace an existing file; choose another name instead.

Choose the user's session and artifact, not a similar-looking one. `session show` returns `data.artifacts` for generated images, local transforms, model results, and contact sheets, including historical outputs. Check each artifact's `available` and `path`; unavailable files retain their records. `data.references` lists turn references and `data.links` lists legacy session ownership. Import remaps IDs and resolves duplicate session names. Session deletion is logical and frees its name. Exports omit credentials and private storage. Open the actual output for the user; a successful command alone does not display it.

## Deterministic postprocessing

```bash
imagen ops --agent
imagen transform art_... --op pixelate:scale=4 --agent
imagen transform art_... --pipeline pipeline.json --agent
imagen compare --first art_a --second art_b --agent
imagen sheet --artifacts art_a,art_b,art_c --columns 3 --agent
```

`transform` requires `--op`, `--pipeline`, or `--preset`. `compare` reports PSNR/SSIM without creating an artifact. A contact sheet infers a common source session; use `--session` for inputs from different sessions. Postprocessing is experimental: metric changes do not establish better perceptual quality.

## Model restoration and runtime installation

`superres` also runs same-size restoration: `film-degrainer` denoises and `dejpg-srformer-light` reduces JPEG artifacts. Neither enlarges the image. Inspect the catalog task, source, and license rather than inferring capabilities from the command name.

Preview runtime installation with `imagen superres install --backend cpu --agent` (no `--model` installs the runtime alone). After the user authorizes installing dependencies, add `--accept`. Available installation backends are `cpu`, `cuda`, and `openvino`. The command targets the current virtual environment, refuses system Python and conflicting ONNX packages, and does not install GPU drivers. Restart Imagen after installation. Dependency installation does not authorize weight downloads or paid provider calls.

Inspect the catalog and let the user select a model and review its source/license. Only download after explicit approval; this example does not select a model for the user:

```bash
imagen superres models --agent
imagen superres fetch --model realesrgan-x4plus --accept --agent
imagen superres run ARTIFACT_ID --model USER_SELECTED_MODEL --backend cpu --tile 256 --overlap 16 --timeout 900 --wait --agent
```

Do not download weights or install inference dependencies automatically. `superres run` needs `--model`; `--accept` authorizes downloading missing weights, so append it only after explicit authorization. Already downloaded weights do not require that flag in the CLI. The older `transform --op 'superres:model=NAME,...'` syntax remains compatible. Report permission/dependency errors. Never substitute ordinary resizing and claim successful model-based upscaling.

## Local jobs, progress, and cancellation

`transform` and `superres run` block by default; `--wait` makes that choice explicit. Processing timeouts default to 120 seconds for ordinary transforms and 900 seconds for model pipelines. `--timeout` stops that computation. Choose the user's model and artifact before using the command below.

```bash
imagen superres run ARTIFACT_ID --model USER_SELECTED_MODEL --async --agent
imagen job get JOB_ID --agent
imagen job wait JOB_ID --timeout 900 --agent
imagen job cancel JOB_ID --scope local --agent
imagen session show SESSION_ID --agent
```

For `--async`, a running worker is required and the accepted result contains `data.job.id`; it is not completed inference. Inspect `worker status` first. Starting a worker also dispatches queued paid generation requests, so `worker start` needs explicit authorization for that queue. Do not start one automatically to make a local command work. `--wait` and `--async` are mutually exclusive.

Human terminal output shows a progress bar on stderr. `--output json` returns one result; `--output jsonl` streams `started`, `progress`, and one `terminal` event. Ordinary pipelines count steps and models count completed tiles; loading, encoding, and saving may have no percentage. `job get` exposes the latest progress in `data.job.progress`; `job wait` observes updates. Its timeout stops waiting, not processing. Local cancellation stops a queued/running local job and does not publish partial images.

After a disk-publication failure or a crashed executor, inspect `job get`, then use `job reconcile JOB_ID --agent` to recover existing staging bytes. It does not recompute or retry inference. Canceled tasks remain canceled. A live executor must finish or stop before reconciliation; a crash before any result was staged has no recoverable output.

The human TUI uses `u` for the model workbench and `s` for runtime/weight management. The model workbench lists real catalog entries, previews only the original, and provides backend, tile, overlap, timeout, and foreground/background controls. Downloads and per-run license confirmation are separate user actions. Closing the model page during a download does not cancel it; local processing has its own cancellation action. Agents use the CLI, not simulated TUI keys.

## Reclaim disk space

```bash
imagen cache audit --agent
imagen cache purge --plan DIGEST --yes --agent
imagen asset gc --dry-run --agent
imagen asset gc --plan DIGEST --yes --agent
```

Present the candidate list and obtain approval before deleting. Use the digest returned by the current dry run; never invent it. Exported ZIPs are unaffected. Artifacts referenced by undeleted sessions are not garbage-collected. Never broaden cleanup to unrelated files.
