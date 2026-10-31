# Imagen workflows

Use the user's language for explanations and approval requests. Keep CLI syntax and machine identifiers unchanged.

## Configure endpoints: OpenAI-compatible first

Prefer an existing user-selected profile. Create or update configuration only when requested. These commands store references, never literal keys:

```bash
imagen profile set local \
  --provider openai --family images \
  --base-url https://gateway.example/v1 \
  --models-path /v1/models \
  --model-default USER_SELECTED_MODEL \
  --credential-ref env:IMAGEN_API_KEY --agent
```

Only when the user chooses Gemini native:

```bash
imagen profile set gemini \
  --provider gemini --family generate_content \
  --base-url https://generativelanguage.googleapis.com/v1beta \
  --models-path /v1beta/models \
  --model-default USER_SELECTED_MODEL \
  --credential-ref env:GEMINI_API_KEY --agent
```

Custom Gemini HTTPS generation URLs must end in `/v1beta`. A gateway may use `/v1/models` for discovery and accept the `nano-banana-2` alias; let the user choose the actual model. Catalog paths replace the base URL's path on the same origin. External URLs, query strings, and fragments are rejected. New profiles default to `/v1/models`; existing profiles retain their setting when the option is omitted.

After authorization to contact the endpoint:

```bash
imagen models refresh local --send-credential --agent
imagen models list local --agent
imagen models use local --id USER_SELECTED_MODEL --agent
```

Gemini discovery prefers `models`; it accepts `data[].id` only when that field is absent, without additional requests. Discovery does not prove generation availability. Gemini native supports generation, image editing, and private continuation context, but not masks. Gateway compatibility is not guaranteed.

Updating with `profile set` requires `--revision CURRENT_REVISION`; query `profile show PROFILE` first. `profile update` takes `--config-file`, not individual fields. A user-authorized key-file import uses `profile secret PROFILE --key-file PATH`; never read or display the key yourself.

## Geometry and model limits

These are this build's local acceptance rules, not proof that a gateway serves the model or returns the requested dimensions. Consult installed `schema generate` / `schema edit`; the offline plan validates the selected route and model.

- Only `openai/images` and `gemini/generate_content` dispatch. Other profiles fail with `unsupported_capability` before a plan exists. Keep the user's endpoint/model; report the limit instead of switching silently.
- OpenAI `size` accepts `auto`, `1024x1024`, `1536x1024`, and `1024x1536`. The `gpt-image-2*` family also accepts `WIDTHxHEIGHT`: both edges divisible by 16, ratio 1:3–3:1, each edge at most 3840, total pixels 655,360–8,294,400. `count` is 1–10.
- OpenAI `provider_params.quality` accepts `auto`, `low`, `medium`, `high`; `gpt-image-2.5*` also accepts `xhigh`, `max`. `input_fidelity` is edit-only. Never duplicate `count` or geometry in `provider_params`.
- Namespaced model IDs such as `A6N/gpt-image-2.5-sunburst` retain the full ID in requests; local model-family checks use its last slash-delimited segment. This does not establish gateway compatibility.
- Gemini uses top-level `aspect_ratio` and `image_size`, never `size`. Ratios: `1:1`, `1:4`, `4:1`, `1:8`, `8:1`, `2:3`, `3:2`, `3:4`, `4:3`, `4:5`, `5:4`, `9:16`, `16:9`, `21:9`. `count` must be 1.

| Gemini model | Accepted `image_size` |
|---|---|
| `gemini-3.1-flash-image` | `512`, `1K`, `2K`, `4K` |
| `gemini-3-pro-image`, `gemini-nano-banana-2.1`, gateway alias `nano-banana-2` | `1K`, `2K`, `4K` |
| `gemini-3.1-flash-lite-image` | `1K` |
| `gemini-2.5-flash-image` | Omit `image_size`; no tier control |

Use uppercase `K`; `2k` is invalid. Explain invalid requests and obtain the user's corrected choice rather than silently changing size, quality, model, or count.

Locally refused IDs: OpenAI `gpt-image-1`, `dall-e-2`, `dall-e-3`; Gemini `gemini-3-pro-image-preview`, `gemini-3.1-flash-image-preview`. A stale catalog cannot override this refusal. Do not select a replacement without the user.

For a user-selected OpenAI 2.5-family model, a 1536×864, xhigh request is:

```json
{
  "schema_version": 1,
  "operation": "generate",
  "session": "ses_REPLACE",
  "profile": "USER_SELECTED_OPENAI_PROFILE",
  "model": "A6N/gpt-image-2.5-sunburst",
  "prompt": "USER_APPROVED_PROMPT",
  "count": 1,
  "size": "1536x864",
  "provider_params": {"quality": "xhigh"}
}
```

For the user's Gemini `gemini-3.1-flash-image` request, use their Gemini profile/model, remove `size` and `provider_params.quality`, and set `"aspect_ratio": "16:9", "image_size": "2K"` at the top level. Unspecified geometry is omitted, never empty or `null`.

```bash
imagen generate --input-json request.json --dry-run --agent
```

Expect `planned` with `data.plan.normalized_parameters`; no creation request is sent. Without `--dry-run` (and without `--async`), expect `approval_required`. A valid plan is not a generated artifact or proof of remote availability. `validation_error` / `unsupported_capability` exit with 2; retain the error's reason and do not approve a refused request.

## Create and execute

Create a user-named session with `imagen session create NAME --agent`, or reuse their selected session. Put its returned ID in `request.json`:

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

For `approval_required`, read `data.plan.id` and `data.plan.plan_digest`, present the plan, and obtain authorization before execution:

```bash
imagen plan approve PLAN_ID --digest PLAN_DIGEST --agent
imagen plan execute PLAN_ID --agent
```

Check `data.artifacts` and job facts. A `null` cost is unknown, not zero. Editing uses `operation=edit`; inspect `imagen schema edit` for input fields. Do not assume a listed model supports editing.

## Recover without resubmitting

```bash
imagen job get JOB_ID --agent
imagen job reconcile JOB_ID --agent
imagen job fetch JOB_ID --agent
```

Inspect facts first. These commands do not authorize another creation request. Consider `plan retry` only for a definitely failed or canceled attempt; its new plan still needs authorization. Stop on unknown outcomes.

## Local processing and weights

```bash
imagen ops --agent
imagen superres models --agent
```

Postprocessing is experimental: smoothing can damage thin lines, sharpening can add noise, and upscaling can invent texture. Dependencies, licenses, and download availability come from the current catalog.

Only after the user chooses the model, reviews its source/license, and authorizes downloading:

```bash
imagen superres fetch --model realesrgan-x4plus --accept --agent
```

Never choose this example model or append `--accept` automatically. Report missing inference dependencies; do not install them without permission. Downloaded weights do not prove inference works.

For a user-selected downloaded model, use `imagen superres run ARTIFACT_ID --model USER_SELECTED_MODEL --wait --agent`. Local `transform` and `superres run` block by default; `--async` returns `data.job.id` and requires a live worker. Check `worker status`; do not start a worker automatically, because it also executes queued paid requests. Obtain authorization for that queue before `worker start`.

`job get` exposes local progress and outputs. `job wait JOB_ID --timeout 900 --agent` stops waiting when its timeout expires; processing continues. A timeout on `transform` or `superres run` stops computation; its deadline and the working-memory cap are agent-accessible controls (`--timeout`, `IMAGEN_POSTPROCESS_TIMEOUT`, `IMAGEN_POSTPROCESS_MEMORY_MB`), the same as for a human. Cancel a local task with `job cancel JOB_ID --scope local --agent`. `session show SESSION_ID --agent` includes all output types in `data.artifacts`, with `available` and `path`. For stages, JSONL includes progress events; JSON keeps one final result. See `imagen-assets` for local job recovery and model-workbench controls.

## Install or update these guides

```bash
imagen skills install --target codex --target claude-code --agent
imagen skills list --target codex --agent
imagen skills update --target codex --agent
imagen skills uninstall --target codex --agent
```

The default scope is project; `--scope global` affects other projects. `--target all` may create many agent directories: explain the scope first. Report conflicts or modified installed guides rather than adding `--force` or `--yes` without approval.
