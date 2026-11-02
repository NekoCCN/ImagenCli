# SaladCloud deployment

One image, two modes. Both run `deploy/worker/Dockerfile` as the base.
Initial authentication, secrets and client registration are covered in
`deploy/README.md`.

## `single-replica-sync` (short jobs)

- Container group replica count: **1**. The worker holds a single in-process job
  slot; a second concurrent request returns `429`.
- `IMAGEN_WORKER_STATE_MODE=single-replica-sync`
- Allowed operations: `watermark.identify`, `watermark.metadata`,
  `watermark.visible`.
- `max_seconds` above **90 s** is rejected with `422 sync_unsupported` before any
  work starts. Salad Container Gateway caps a response at 100 s.
- `POST /v1/jobs` returns the full result inline, including base64 output.

## `queue` (long jobs)

Salad requires the official **Queue Worker** binary inside the image; the app
never polls the queue itself. `deploy/salad/queue-worker.Dockerfile` wraps the
base image with s6-overlay and supervises both processes:

```bash
docker build -f deploy/salad/queue-worker.Dockerfile \
  --build-arg BASE=imagen-worker:0.1.17-raiw0.53.0 \
  --build-arg SALAD_QUEUE_WORKER_URL=https://.../salad-queue-worker \
  -t imagen-worker-queue:0.1.17-raiw0.53.0 .
```

- `SALAD_QUEUE_WORKER_ARGS` passes extra flags to the vendor binary.
- `deploy/salad/s6/` holds the two supervised services (`web`, `queue-worker`).

- `IMAGEN_WORKER_STATE_MODE=queue`
- `SALAD_QUEUE_ORGANIZATION`, `SALAD_QUEUE_PROJECT`, `SALAD_QUEUE_NAME`,
  `SALAD_API_KEY`, `SALAD_S4_*` (set by the container group, not the image).
- `POST /v1/jobs` returns `202 {job_id, status}`.
- The queue connection is fixed when the container group is created; changing
  the queue requires a new group.
- Salad retries a failed queue message up to **4** times. Records expose
  `attempts` and `max_attempts=4`; the client never auto-retries.

## Auth

Every route except `/healthz` and `/readyz` requires
`Authorization: Bearer $IMAGEN_WORKER_TOKEN`. Salad Container Gateway
additionally requires `Salad-Api-Key`; the image does not implement that layer.

## Not done here

Publishing the image to `ghcr.io` and creating Salad container groups are
outward-facing actions and need explicit authorization.
