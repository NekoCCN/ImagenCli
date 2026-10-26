---
name: imagen
version: 1.1.0
description: Use when configuring image endpoints or models, generating or editing images, or managing image tasks with the Imagen CLI.
---

# Imagen CLI

Use Imagen's CLI to work with configured image providers and a persistent local workspace. Local storage does not mean generation runs locally.

## Communication

Explain choices, approvals, errors, and results in the user's language. Keep commands, flags, JSON keys, status values, profile IDs, and model IDs unchanged. Preserve the user's prompt language unless they request translation. This English guide does not require English user-facing replies.

## Discover first

```bash
imagen help --agent
imagen schema generate
imagen profile list --agent
```

Use the installed version's help and schema; do not guess flags. For machine calls, use `--agent` or `--output json` and check the exit code plus `ok/status/data/error`. Ask the user to choose when profiles or models are missing or ambiguous. Never print credentials or inspect secret files to discover a key.

## Providers and models

- OpenAI-compatible Images (`openai` / `images`) is the primary setup. Gemini native (`gemini` / `generate_content`) is optional. A model name or catalog schema does not select the generation protocol.
- Prefer the user's selected profile and catalog model. If discovery fails, accept a user-supplied ID; do not silently switch provider, endpoint, or model.
- `profile set PROFILE --models-path PATH` configures a same-origin catalog path independently of the generation URL. New profiles default to `/v1/models`; Google's native catalog needs `/v1beta/models`. Omitting the option when updating preserves the existing setting.
- Every `models refresh` contacts the endpoint and requires network authorization. Adding `--send-credential` also sends the configured credential and requires approval for that disclosure. Without the flag, discovery sends no credential. A catalog entry is not capability or availability evidence. Do not probe alternate paths automatically.
- Agents use the CLI. The human TUI uses selectors with custom text fallbacks; both share validation and execution logic.

See [workflows](references/workflows.md) for configuration and request examples.

## Generate and edit

1. Confirm prompt, inputs, profile, model, and count. Explain where inputs will be sent.
2. Create or reuse the selected session. `generate` or `edit` freezes a plan without sending a creation request.
3. Present the plan's scope, destination, and available cost information. `approval_required` is pending approval, not failure or generation success.
4. Only after explicit authorization for this request, call `plan approve` and `plan execute`.
5. Read actual artifact IDs and paths from the result. Open the output with `asset open`. A `null` cost is unknown, not free.

`--async` and `batch submit` can queue or execute paid requests; they require authorization for that scope, not just permission to plan.

## Unknown outcomes

For `unknown`, `outcome_unknown`, or `accepted_maybe=true`, stop creation and inspect local `job get` / `job reconcile` facts. Do not regenerate or create a new session to bypass uncertainty.

## Related tasks

Use `imagen-assets` for sessions, artifacts, transforms, exports, and upscaling; use `imagen-batch` for batches. Postprocessing is experimental and creates new artifacts. Never overwrite originals. Weight downloads, inference dependency installs, deletion, and forced overwrites require explicit approval.
