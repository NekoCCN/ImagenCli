# Salad queue-mode image: the same worker plus the vendor Queue Worker binary.
#
# The app never polls the queue. s6-overlay supervises uvicorn and the vendor
# binary in one container. Build from the repository root, after building the
# base image described in deploy/worker/Dockerfile:
#
#   docker build -f deploy/salad/queue-worker.Dockerfile \
#     --build-arg BASE=imagen-worker:0.1.17-raiw0.53.0 \
#     --build-arg SALAD_QUEUE_WORKER_URL=https://.../salad-queue-worker \
#     -t imagen-worker-queue:0.1.17-raiw0.53.0 .
#
# Publishing this image and creating container groups are outward-facing
# actions and need explicit authorization.
ARG BASE=imagen-worker:0.1.17-raiw0.53.0
FROM ${BASE}

USER root

ARG S6_OVERLAY_VERSION=3.2.0.2
ARG TARGETARCH=amd64
ARG SALAD_QUEUE_WORKER_URL

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl xz-utils ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL "https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-noarch.tar.xz" \
        -o /tmp/s6-noarch.tar.xz \
    && curl -fsSL "https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-${TARGETARCH}.tar.xz" \
        -o /tmp/s6-arch.tar.xz \
    && tar -C / -Jxpf /tmp/s6-noarch.tar.xz \
    && tar -C / -Jxpf /tmp/s6-arch.tar.xz \
    && rm -f /tmp/s6-noarch.tar.xz /tmp/s6-arch.tar.xz

RUN test -n "${SALAD_QUEUE_WORKER_URL}" \
        || (echo "SALAD_QUEUE_WORKER_URL is required" >&2 && exit 1) \
    && curl -fsSL "${SALAD_QUEUE_WORKER_URL}" -o /usr/local/bin/salad-queue-worker \
    && chmod 0755 /usr/local/bin/salad-queue-worker

COPY deploy/salad/s6/ /etc/s6-overlay/s6-rc.d/

ENV IMAGEN_WORKER_STATE_MODE=queue \
    S6_BEHAVIOUR_IF_STAGE2_FAILS=2

ENTRYPOINT ["/init"]
CMD []
