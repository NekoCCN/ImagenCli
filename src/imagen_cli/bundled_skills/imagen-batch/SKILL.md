---
name: imagen-batch
version: 1.1.0
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

## Inspect and wait

```bash
imagen batch get bat_... --agent
imagen batch wait bat_... --timeout 600 --agent
```

Judge success per child, including saved artifacts. `batch wait` observes only; it does not retry. Never resubmit the entire batch automatically after partial failure. Replan only definitely failed children with fresh authorization. For `unknown`, `outcome_unknown`, or `accepted_maybe=true`, stop creation and inspect the corresponding job's local facts before asking the user what to do.
