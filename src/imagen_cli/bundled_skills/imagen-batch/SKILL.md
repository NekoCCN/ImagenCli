---
name: imagen-batch
version: 1.2.0
description: Use when submitting multiple Imagen image requests, choosing provider concurrency, inspecting batch child jobs, or handling partial batch failures.
---

# Imagen batch requests

Explain scope, approvals, progress, and results in the user's language. Keep CLI syntax, JSON keys, status values, and model/profile IDs unchanged. Preserve each user-approved prompt's language.

Each batch child is an independent turn/job. One child's failure does not overwrite another's results.

## Submit only after authorization

`batch submit` approves and executes child requests; it does not merely create reviewable plans. Confirm all prompts, endpoints, models, counts, and potential costs before submitting. Do not choose a different provider to make the batch work. OpenAI-compatible endpoints are the primary setup; Gemini native is optional and must be explicitly selected through the configured profile.

Prepare a JSON file with a `requests` array and `concurrency` from 1 to 8:

```json
{
  "concurrency": 2,
  "requests": [
    {
      "schema_version": 1,
      "operation": "generate",
      "session": "ses_REPLACE",
      "profile": "USER_SELECTED_PROFILE",
      "prompt": "USER_APPROVED_PROMPT_1",
      "count": 1
    },
    {
      "schema_version": 1,
      "operation": "generate",
      "session": "ses_REPLACE",
      "profile": "USER_SELECTED_PROFILE",
      "prompt": "USER_APPROVED_PROMPT_2",
      "count": 1
    }
  ]
}
```

```bash
imagen batch submit --input-json requests.json --agent
```

Read `data.batch.id` and each child's result. `--input-json -` reads stdin. All children are validated offline before submission; invalid input rejects the batch. Effective concurrency is bounded by the requested value, 8, and declared provider limits. A `null` cost is unknown, not free.

For cost preview only, create individual unapproved `generate` / `edit` plans using the `imagen` workflow; do not call `batch submit`, approve, or execute them. Available costs may remain unknown.

## Per-child parameters

Use the selected profile's route for each child; a mixed-provider batch has no shared geometry default. This build dispatches only `openai/images` and `gemini/generate_content`; a catalog entry on another route does not make it executable.

| Child route | Top-level fields | `count` |
|---|---|---|
| `openai/images` | `size`, e.g. `1536x864` on `gpt-image-2*`, or `auto`; `provider_params: {"quality": "xhigh"}` on `gpt-image-2.5*` | 1–10 |
| `gemini/generate_content` | `aspect_ratio`, e.g. `16:9`; `image_size`, e.g. `2K` for `gemini-3.1-flash-image`; omit `size` | 1 |

Keep geometry and count at the top level; put OpenAI quality inside `provider_params`. Omit unspecified fields, not empty strings or `null`. Gemini `2k` is invalid (uppercase `K`); never silently reduce count or change requested parameters. **REQUIRED SUB-SKILL:** Use `imagen` and its linked workflows for model-specific size/tier limits, retired IDs, and planning.

For preview, write each child object to its own JSON file and run `imagen generate --input-json child.json --dry-run --agent` (use `edit` for edit children). Check the exit code and `status=planned`, then present `data.plan.normalized_parameters`, target, and available costs. A refused child has no executable plan. This sends no creation request; neither `batch submit` nor `--async` is a preview command.

## Inspect and wait

```bash
imagen batch get bat_... --agent
imagen batch wait bat_... --timeout 600 --agent
```

Judge success per child, including saved artifacts. `batch wait` observes only; it does not retry. Never resubmit the entire batch automatically after partial failure. Replan only definitely failed children with fresh authorization. For `unknown`, `outcome_unknown`, or `accepted_maybe=true`, stop creation and inspect the corresponding job's local facts before asking the user what to do.
