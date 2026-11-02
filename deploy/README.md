# Remote worker environments

One worker image, two clouds. Deployment tooling is an optional extra, so a
normal `imagen` install never pulls in the Modal SDK:

```bash
# Repository checkout. The deploy/ files are not shipped inside the wheel, so
# deployment always runs from a clone.
uv sync --extra deploy --extra ai-onnx
.venv/bin/modal --version
```

`uv sync` is exact: it removes extras you leave out. Name every extra you use
on the same command line.

SaladCloud needs no Python package here. Container groups are created in its
portal (or REST API), and queue mode downloads the vendor Queue Worker binary
at image build time. See `deploy/salad/README.md`.

## Build the image once

```bash
docker build -f deploy/worker/Dockerfile \
  --build-arg IMAGEN_FROM_SOURCE=1 \
  -t imagen-worker:dev .
```

`IMAGEN_FROM_SOURCE=1` installs the checked-out source; without it the image
installs `imagen-cli` from PyPI. Smoke-test the endpoints before deploying:

```bash
docker run --rm -p 8000:8000 \
  -e IMAGEN_WORKER_TOKEN=test-token \
  -e IMAGEN_WORKER_STATE_MODE=single-replica-sync \
  imagen-worker:dev
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS -H "Authorization: Bearer test-token" http://127.0.0.1:8000/v1/meta
```

## Published dev image

`ghcr.io/nekoccn/imagen-worker:dev` is built from this checkout (amd64) and is a
public package, so Modal and Salad can pull it without registry credentials.
Verify anonymous access with:

```bash
token=$(curl -s "https://ghcr.io/token?service=ghcr.io&scope=repository:nekoccn/imagen-worker:pull" | jq -r .token)
curl -fsS -H "Authorization: Bearer $token" \
  -H "Accept: application/vnd.oci.image.manifest.v1+json" \
  https://ghcr.io/v2/nekoccn/imagen-worker/manifests/dev >/dev/null
```

## Modal

Authentication is interactive and belongs to your browser session. Check for an
existing profile first; otherwise run `modal setup` yourself so the token lands
in `~/.modal.toml` and never in this repository:

```bash
.venv/bin/modal token info     # exits non-zero when no profile is configured
.venv/bin/modal setup          # opens a browser; prints a URL when it cannot
```

Create the worker secret. The token value must match the one the client will
send as `Authorization: Bearer`. `deploy/env.example` lists the variables; a
real local file such as `deploy/.env.local` (git-ignored, mode 0600) can be read
without putting values on a command line. Select **only** the worker key so
unrelated credentials in that file never leave the machine:

```python
# once in the repository checkout, with the deploy extra installed
from pathlib import Path
import modal
lines = Path("deploy/.env.local").read_text().splitlines()
token = next(line.split("=", 1)[1] for line in lines if line.startswith("IMAGEN_WORKER_TOKEN="))
modal.Secret.objects.create("imagen-worker-auth", {"IMAGEN_WORKER_TOKEN": token})
```

The Hugging Face secret is optional because its value is not generated here and
most models are not gated:

```bash
.venv/bin/modal secret create imagen-hf HF_TOKEN=<hugging-face-token>
```

`imagen-hf` is only attached when `IMAGEN_HF_SECRET=imagen-hf` is set during
deploy. Then deploy and note the printed web URL; the ASGI app is served at its
root. `T4` is the cheapest GPU; the app scales to zero when idle:

```bash
IMAGEN_WORKER_IMAGE=ghcr.io/nekoccn/imagen-worker:dev \
IMAGEN_MODAL_GPU=T4 \
  .venv/bin/modal deploy deploy/modal_app.py
```

The first deploy creates the `imagen-hf-cache` and `imagen-jobs` volumes. To
register the endpoint, export the bearer token in the current shell first
(`imagen remote add` probes `/v1/meta` immediately):
```bash
export IMAGEN_WORKER_TOKEN=<bearer-token>
imagen remote add modal \
  --endpoint https://<workspace>--imagen-worker-web.modal.run \
  --credential-ref env:IMAGEN_WORKER_TOKEN
imagen remote status modal
```

### Invisible watermark runs

`watermark.invisible` needs the `qwen-zimage` stack: Qwen-Image-2512 (20B), its
Canny ControlNet, the Lightning LoRA, Z-Image-Turbo and SAM. The first run
downloads roughly 60 GB into `imagen-hf-cache`; later runs reuse it. Use a 24 GB
card with CPU offload and give the function enough RAM:

```bash
IMAGEN_WORKER_IMAGE=ghcr.io/nekoccn/imagen-worker@sha256:<digest> \
IMAGEN_MODAL_GPU=A10G IMAGEN_MODAL_MEMORY_MB=32768 IMAGEN_MODAL_CPU=8 \
  .venv/bin/modal deploy deploy/modal_app.py
```

Pin the image by digest: Modal caches built images per image definition, so a
moving tag can keep resolving to an older cached build. Shared-volume mode
returns `202` before the job runs, and `max_containers=1` keeps every poll on the
same process.

## SaladCloud

1. Push `imagen-worker:dev` to a registry the container group can pull. Pushing
   and creating cloud resources are outward-facing actions; do them only after
   explicit authorization.
2. Create a container group from that image with:
   - HTTP port `8000`, health check `GET /healthz`
   - replicas `1` for `single-replica-sync` (the worker allows one job at a
     time; a second concurrent request gets `429`)
   - environment: `IMAGEN_WORKER_TOKEN`,
     `IMAGEN_WORKER_STATE_MODE=single-replica-sync`, `IMAGEN_JOBS_ROOT=/jobs`,
     plus optional metadata such as `IMAGEN_WORKER_VERSION`,
     `IMAGEN_CLI_VERSION`, `IMAGEN_RAIW_VERSION`, `IMAGEN_WORKER_DIGEST`,
     `IMAGEN_WORKER_PROVIDERS=cuda`

   The Container Gateway reaches containers over IPv6, so the image binds
   `::` via uvicorn (see `deploy/worker/Dockerfile`). A command override that
   binds `0.0.0.0` makes every gateway request return `503`.
3. Register the gateway URL. Salad's Container Gateway authenticates with a
   Salad API key in addition to the worker's own bearer token:

```bash
export SALAD_API_KEY=<salad-api-key>
imagen remote add salad \
  --endpoint https://<group>.salad.cloud \
  --credential-ref env:IMAGEN_WORKER_TOKEN \
  --gateway-credential-ref env:SALAD_API_KEY
imagen remote status salad
```

Queue mode (long jobs) needs the vendor Queue Worker binary and a queue name;
build it with `deploy/salad/queue-worker.Dockerfile` and follow
`deploy/salad/README.md`.

## Credentials

`imagen` stores only `env:NAME` references in its config; the values stay in
your shell and are never copied into the config file or the job database. Use
`deploy/env.example` as the checklist and keep real values out of the
repository. `deploy/.env.local` is git-ignored if you prefer a file.

## Verify

```bash
curl -fsS "$ENDPOINT/healthz"
curl -fsS "$ENDPOINT/readyz"
curl -fsS -H "Authorization: Bearer $IMAGEN_WORKER_TOKEN" "$ENDPOINT/v1/meta"
```

`/readyz` returns non-200 until the jobs directory exists (Modal volume or the
container's `/jobs`). `/v1/meta` reports `state_mode`, `gpu`, `raiw_version`,
`max_attempts` and the worker digest.
