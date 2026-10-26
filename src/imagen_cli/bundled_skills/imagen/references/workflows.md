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

## Install or update these guides

```bash
imagen skills install --target codex --target claude-code --agent
imagen skills list --target codex --agent
imagen skills update --target codex --agent
imagen skills uninstall --target codex --agent
```

The default scope is project; `--scope global` affects other projects. `--target all` may create many agent directories: explain the scope first. Report conflicts or modified installed guides rather than adding `--force` or `--yes` without approval.
