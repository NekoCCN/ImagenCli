---
name: imagen-assets
version: 1.7.1
description: Use when browsing or resuming Imagen sessions, importing or saving artifacts, applying local transforms, comparing images, upscaling, removing watermarks from images or video in single runs or directory batches, using a remote GPU endpoint, or reclaiming workspace disk space.
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

`transform` requires `--op`, `--pipeline`, or `--preset`. `--op` names one operator with inline parameters (`pixelate:scale=4`); `--preset` names a catalog recipe. Confirm the exact name and its parameters with `imagen ops --agent` and `imagen ops --op NAME --agent` instead of guessing. `compare` reports PSNR/SSIM without creating an artifact. A contact sheet infers a common source session; use `--session` for inputs from different sessions. Postprocessing is experimental: metric changes do not establish better perceptual quality.

## Model restoration and runtime installation

`superres` also runs same-size restoration: `film-degrainer` denoises and `dejpg-srformer-light` reduces JPEG artifacts. Neither enlarges the image. Inspect the catalog task, source, and license rather than inferring capabilities from the command name.

Preview runtime installation with `imagen superres install --backend cpu --agent` (no `--model` installs the runtime alone). After the user authorizes installing dependencies, add `--accept`. Available installation backends are `cpu`, `cuda`, and `openvino`. The command targets the current virtual environment, refuses system Python and conflicting ONNX packages, and does not install GPU drivers. Restart Imagen after installation. Dependency installation does not authorize weight downloads or paid provider calls.

Inspect the catalog and let the user select a model and review its source/license. Only download after explicit approval; this example does not select a model for the user:

```bash
imagen superres models --agent
imagen superres doctor --backend cuda --agent
imagen superres fetch --model realesrgan-x4plus --accept --agent
imagen superres run ARTIFACT_ID --model USER_SELECTED_MODEL --backend cpu --tile 256 --overlap 16 --timeout 900 --wait --agent
```

`superres doctor` runs one tile on a 32×32 sample and reports which provider actually ran; use it before trusting a `--backend` choice. It proves the backend, not output quality.

Do not download weights or install inference dependencies automatically. `superres run` needs `--model`; `--accept` authorizes downloading missing weights, so append it only after explicit authorization. Already downloaded weights do not require that flag in the CLI. The older `transform --op 'superres:model=NAME,...'` syntax remains compatible. Report permission/dependency errors. Never substitute ordinary resizing and claim successful model-based upscaling.

## Watermark inspection and removal

Watermark work reads a published artifact and always writes a new artifact; the source stays untouched. Start with the read-only checks and report what they actually found instead of assuming a mark exists.

```bash
imagen watermark status --agent
imagen watermark identify ARTIFACT_ID --agent
imagen watermark metadata ARTIFACT_ID --agent
imagen watermark classify ARTIFACT_ID --agent
imagen watermark classify ARTIFACT_ID --source --agent
imagen watermark metadata ARTIFACT_ID --remove --agent
imagen watermark visible ARTIFACT_ID --agent
imagen watermark visible ARTIFACT_ID --backend lama --mark NAME --agent
imagen watermark erase ARTIFACT_ID --region 1640,1930,400,100 --agent
imagen watermark invisible ARTIFACT_ID --pipeline sdxl-zimage --agent
imagen watermark all ARTIFACT_ID --pipeline sdxl-zimage --agent
```

`identify` and `metadata` without `--remove` publish nothing. `classify` is also read-only: by default it classifies a photo from pixels; with `--source` it classifies the likely source pipeline. Neither is provenance and neither detects SynthID. `metadata --remove` strips AI metadata (C2PA, EXIF, XMP, IPTC, PNG text) into a copy. `visible` removes registered visible marks with a CPU backend (`cv2`, `migan`, `lama`); `--mark NAME` targets one registered mark and `--sensitivity strict` keeps the high-precision gate. `erase` inpaints every `--region x,y,width,height` box, for marks the registry does not cover; boxes must lie inside the image. `invisible` regenerates pixels through an image model to disrupt invisible watermarks such as SynthID-class marks. `all` runs visible, invisible, and metadata removal in order and skips the invisible stage when there is no signal or no GPU. No local SynthID pixel detector exists, so never claim a hidden mark was verified as gone. Regeneration changes detail and lowers fidelity; never describe it as lossless, guaranteed, or a quality improvement. Metadata removal is not pixel-level watermark removal.

`invisible` and `all` need a GPU and the isolated `remove-ai-watermarks` environment. `watermark status` reports readiness, version, capabilities, and the memory cap; `watermark setup` previews and `--accept` creates the environment. Add the extras the task needs: `--extras visible` for registered visible marks, `--extras visible,qwen-zimage` for invisible or `all`, `--extras visible,classify,source-classify` for classification, and `--extras visible,video,diffusion` for video. `watermark update --accept` builds and probes a new environment before switching, keeping the old one on failure. Each pipeline carries its own license: state it, then pass `--accept-license` only after the user confirms that pipeline. A run estimated above the memory cap returns `approval_required` with `data.budget.digest`; repeat the identical command with `--budget-approval DIGEST`. Use `--tile`, `--tile-size`, `--tile-overlap`, `--cpu-offload`, `--force`, `--strength`, `--seed`, `--humanize`, `--unsharp`, `--controlnet-conditioning-scale`, `--adaptive-polish`, `--text-manifest`, `--fidelity-anchor`, and `--max-resolution` only when the source or hardware needs them, and `--max-seconds` to bound the run.

## Video watermarks

Videos are imported with `session import-image`; `ffmpeg` and `ffprobe` must be on `PATH`. Video operations always publish a new artifact except `video visible`, which writes nothing when no stable mark matches.

```bash
imagen session import-image SESSION_ID clip.mp4 --agent
imagen watermark video identify ARTIFACT_ID --agent
imagen watermark video metadata ARTIFACT_ID --agent
imagen watermark video metadata ARTIFACT_ID --remove --agent
imagen watermark video visible ARTIFACT_ID --mark auto --agent
imagen watermark video all ARTIFACT_ID --agent
imagen watermark video invisible ARTIFACT_ID --noise-std 0.15 --agent
```

`video identify` is read-only. `video metadata --remove` remuxes with stream copy and does not transcode. `video visible` requires a mark that repeats at a stable position across adjacent frames; a miss publishes nothing, so do not claim cleanup. `video all` always writes an output and can add lossy VAE regeneration with `--include-invisible`. `video invisible` shares one noise field across frames, changes resolution, frame rate, and detail, and cannot certify SynthID removal; the audio watermark stays unverified.

## Directory batches

```bash
imagen watermark batch ./images --mode visible --to ./images_clean --agent
imagen watermark batch ./images --mode all --accept-license --to ./images_clean --agent
imagen watermark video batch ./videos --mode all --to ./videos_clean --agent
```

Image modes are `visible`, `metadata`, `all`, `invisible`; video modes are `all`, `visible`, `metadata`. The output directory must be outside the source and empty; existing files are never replaced. A per-file failure appears in `data.report.errors` and does not stop the batch. Batches write plain files, not managed artifacts, so they create no session or artifact records and no per-run budget digest; image `all` and `invisible` still require `--accept-license`. Report the counts and every error from the report rather than claiming a clean directory.

## Remote GPU endpoints

Watermark work can run on a registered remote worker instead of the local GPU. Register an endpoint only when the user authorized that endpoint and supplied a credential reference; never read, print, or embed the credential itself.

```bash
imagen remote add worker --endpoint https://worker.example --credential-ref env:WORKER_TOKEN --agent
imagen remote list --agent
imagen remote status worker --agent
imagen remote jobs list --agent
imagen remote job get REMOTE_JOB_ID --agent
```

`remote add` probes `/v1/meta` and stores nothing if the probe fails. The first `watermark ... --remote worker` submission returns `approval_required` with `data.job_digest` because it uploads the image; show the digest and the destination, ask for authorization, then rerun the identical command with `--accept-remote DIGEST`. The returned artifact records the provider, GPU, worker digest, and remote status in its origin manifest.

```bash
imagen watermark invisible ARTIFACT_ID --remote worker --pipeline sdxl-zimage --accept-license --agent
imagen remote job resume REMOTE_JOB_ID --agent
imagen remote job retry-interrupted REMOTE_JOB_ID --agent
imagen remote job cancel REMOTE_JOB_ID --agent
```

`remote job resume` re-reads a queued, running, or succeeded job and fetches its output; `remote job retry-interrupted` reuses the same digest only for an interrupted job; `remote job cancel` records the worker's answer. On `unknown` or `accepted_maybe`, stop and inspect the recorded job; do not resubmit, and do not open a new session to route around uncertainty. A sync endpoint answers inline within its own short limit; a queue or shared-volume endpoint is polled for up to `--max-seconds`. Create signing or gateway credentials outside Imagen and reference them only as `env:`, `keyring:`, or `private:`.

## Local jobs, progress, and cancellation

`transform` and `superres run` block by default; `--wait` makes that choice explicit. The processing deadline scales with image size and the selected operators, and model pipelines get a longer one; `--timeout` fixes it for that run and `--timeout 0` disables it. Agents have the same controls as users: `--timeout`, `--budget-approval DIGEST`, `IMAGEN_POSTPROCESS_TIMEOUT`, and `IMAGEN_POSTPROCESS_MEMORY_MB` (MiB, `0` removes the cap), with the same values storable in `config.json` as `postprocess_timeout_seconds` and `postprocess_memory_mb`. A run estimated above the memory cap returns `approval_required` with `data.budget.digest`; rerun the same command with `--budget-approval DIGEST`, or answer `y` to the interactive prompt. Choose the user's model and artifact before using the command below.

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

The human TUI uses `u` for the model workbench and `s` for runtime/weight management. The model workbench lists real catalog entries, previews only the original, and provides backend, tile, overlap, timeout (0 disables the deadline), and foreground/background controls. Downloads and per-run license confirmation are separate user actions. Closing the model page during a download does not cancel it; local processing has its own cancellation action. Agents use the CLI, not simulated TUI keys.

## Reclaim disk space

```bash
imagen cache audit --agent
imagen cache purge --plan DIGEST --yes --agent
imagen asset gc --dry-run --agent
imagen asset gc --plan DIGEST --yes --agent
```

Present the candidate list and obtain approval before deleting. Use the digest returned by the current dry run; never invent it. Exported ZIPs are unaffected. Artifacts referenced by undeleted sessions are not garbage-collected. Never broaden cleanup to unrelated files.
