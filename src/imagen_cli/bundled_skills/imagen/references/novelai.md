# NovelAI-compatible workflow

## Boundaries before configuration

Offline protocol tests are not live compatibility verification. Model support is unknown until
confirmed for the selected service; manual model IDs are not capability claims. No inpainting,
streaming previews, tag suggestions, Director Tools augmentation, upscaling endpoints,
subscription/account management, balance tracking, price tables or automatic cost optimization.
JSON generation only: multipart-only servers are not claimed compatible. Never add quality tags,
negative prompts, substitute samplers/models, clamp invalid values or split count into hidden calls.
Count defaults to one; larger counts require declared capability. Pricing and encoding charges are
unknown. Do not infer subscription benefits or query an account/balance.

## Configure locally

```bash
imagen profile set novelai --provider novelai --family generate_image --base-url https://images.example.test/prefix --model-default USER_SELECTED_MODEL --credential-ref env:IMAGEN_API_KEY
imagen session create novelai-work --agent
imagen schema generate
imagen schema vibe plan
```

Bearer and encoded references are defaults. To select options explicitly on a new profile,
save `profile.toml` (use a different name if the profile already exists):

```toml
id = "novelai"
name = "novelai"
provider = "novelai"
family = "generate_image"
base_url = "https://images.example.test/prefix"
model_default = "USER_SELECTED_MODEL"
credential_ref = "env:IMAGEN_API_KEY"
revision = 1
[novelai_options]
auth_mode = "bearer"
reference_transport = "encoded"
[trust_policy]
allowed_upload_targets = ["https://images.example.test/prefix"]
```

```bash
imagen profile add novelai --config-file profile.toml --agent
```

These commands do not contact a provider. Set the referenced environment variable securely, never
put its value in JSON, logs or examples. `novelai_options.auth_mode: "x-api-key"` selects X-API-Key instead of Bearer,
not both. Use the configured HTTPS API root, not a full operation URL: `/prefix` becomes
`/prefix/ai/generate-image` for generation and `/prefix/ai/encode-vibe` for encoding. Trailing slashes
are normalized; a full route would be appended again. No host fallback, redirects, environment
proxies or automatic retries. HTTP is allowed only for explicit loopback development.
Discovery is optional and explicit: configure a documented models path before requesting it;
never probe `/models` or send credentials during planning merely to test a manual model name.

## Choose image roles in structured creation JSON

Use the actual returned session/artifact IDs. Import images explicitly before planning. Primary
`inputs` mean img2img, not inspiration. Img2img uses `operation: "edit"`, exactly one input, no mask,
and explicit `strength` and `noise` in `provider_params`. Keep reference images in `references`
with aligned, ordered `reference_options`, not `inputs` or image blobs in provider parameters.

Example creation input for a prepared Vibe reference (save as `request.json`):

```json
{
  "schema_version": 1,
  "operation": "generate",
  "session": "SESSION_ID",
  "profile": "novelai",
  "model": "USER_SELECTED_MODEL",
  "prompt": "A red kite over a quiet field",
  "count": 1,
  "provider_params": {"steps": 28, "scale": 5.0, "seed": 42},
  "references": ["artifact:ARTIFACT_ID"],
  "reference_options": [{"kind": "vibe", "settings": {"strength": 0.5, "information_extracted": 0.6}}]
}
```

Vibe is inspiration. Precise reference is character or character-and-style guidance: use
`kind: "precise"`, settings `mode: "character"` (or `"character&style"`), `strength` and
optional `fidelity`. Precise inputs are deterministically fit/padded onto supported canvases;
inspect the recorded transformation. Baseline precise and Vibe cannot be mixed. Unsupported
model/reference combinations fail before dispatch, not silently downgrade. Structured prompts,
booleans and numbers use the same validated provider parameters as CLI/TUI; inspect the schema.

## Explicit Vibe encoding approval, then generation approval

Encoded mode requires a separate preparation, potentially charged separately. Planning is offline:

```bash
imagen vibe plan ARTIFACT_ID --profile novelai --model USER_SELECTED_MODEL --information-extracted 0.6 --agent
imagen vibe status PREPARATION_ID --agent
imagen vibe execute PREPARATION_ID --digest DIGEST --agent
```

Replace PREPARATION_ID and DIGEST with the returned values. Review destination, source hash,
model, extraction setting and separate-charge disclosure before approving `vibe execute`.
That command is the encoding approval and may send a paid request; do not run it merely to plan.
Status only observes. Successful encoded bytes are private local cache data, not exported assets.
Reuse requires an exact endpoint/profile identity, source hash, model and extraction setting match;
cache drift or missing data is an error, never hidden paid re-encoding. Generation binds provenance.
Only explicitly documented raw-reference servers may use `novelai_options.reference_transport: "raw"`; never guess
raw support by hostname or model. Raw mode has no hidden encoding request.

```bash
imagen generate --input-json request.json --agent
imagen plan show PLAN_ID --agent
imagen plan approve PLAN_ID --digest PLAN_DIGEST --agent
imagen plan execute PLAN_ID --agent
```

Review all uploads and the frozen request before approval. Check actual returned count and seed
metadata, not just requested values. Queue recovery retains the same roles, ordering, hashes,
transformation and private encoding provenance; it must not prepare or resend automatically.
Unknown completion can already have incurred a charge. Observe status first; any explicit new
attempt needs duplicate-cost acknowledgment and a fresh reviewed approval. Do not invent a price.
TUI exposes separate preparation plan, execute and status commands and the same reference roles.


Vibe encoding defaults to the official JSON contract (base64 `image`, `model`,
`information_extracted`; binary response, including HTTP 201). For a gateway
that documents multipart encoding, explicitly set
`novelai_options.vibe_encoding_transport: "multipart"` in profile set JSON (or the profile add TOML config)
or choose it in the TUI profile editor. This sends one validated image file under
`image` with its detected MIME type, plus ordinary `model` and
`information_extracted` form fields. Generation remains JSON. No hostname
inference, automatic fallback, or retry occurs. Gateway compatibility is tested
offline, not live-verified. Changing this option invalidates prepared approvals
and cached encoding reuse; prepare and approve a new plan. Existing default JSON
preparation identities remain valid, so no cache migration is required.
