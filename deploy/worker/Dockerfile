# Worker image: one image, two clouds.
#
# Build from the repository root:
#   docker build -f deploy/worker/Dockerfile \
#     --build-arg IMAGEN_VERSION=0.1.17 \
#     -t imagen-worker:0.1.17-raiw0.53.0 .
#
# For an unreleased build, install the checked-out source instead of PyPI:
#   docker build -f deploy/worker/Dockerfile \
#     --build-arg IMAGEN_FROM_SOURCE=1 \
#     -t imagen-worker:dev .
#
# The image is not published by this repository without explicit authorization.
FROM python:3.12-slim

ARG IMAGEN_VERSION=0.1.17
ARG IMAGEN_FROM_SOURCE=0
ARG RAIW_SPEC=remove-ai-watermarks[visible,qwen-zimage]>=0.53,<0.54
# Matting needs an ONNX runtime; the weights are a separate BAKED download so a
# worker never fetches them at request time (a mid-job download would time out).
ARG IMAGEN_MATTING=1
ARG IMAGEN_MATTING_MODEL=birefnet-lite
# "gpu" installs the CUDA build plus the pip CUDA/cuDNN wheels it needs; "cpu"
# keeps the image small. onnxruntime-gpu still falls back to the CPU provider on
# a host without a usable driver, so a GPU image is not host-specific.
ARG IMAGEN_MATTING_RUNTIME=cpu

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE /src/
COPY src /src/src

RUN if [ "${IMAGEN_FROM_SOURCE}" = "1" ]; then \
        pip install --no-cache-dir /src; \
    else \
        pip install --no-cache-dir "imagen-cli==${IMAGEN_VERSION}"; \
    fi \
    && pip install --no-cache-dir \
        "${RAIW_SPEC}" \
        "fastapi>=0.115" \
        "uvicorn[standard]>=0.30" \
        "python-multipart>=0.0.12"

RUN if [ "${IMAGEN_MATTING}" = "1" ]; then \
        if [ "${IMAGEN_MATTING_RUNTIME}" = "gpu" ]; then \
            pip install --no-cache-dir \
                "onnxruntime-gpu[cuda,cudnn]>=1.20" "onnx>=1.20" "numpy>=2"; \
        else \
            pip install --no-cache-dir \
                "onnxruntime>=1.20" "onnx>=1.20" "numpy>=2"; \
        fi; \
    fi

COPY deploy/worker/app.py /app/app.py

# The fetch must already see the final weight directory, so set it first.
ENV IMAGEN_JOBS_ROOT=/jobs \
    IMAGEN_MODEL_DIR=/models \
    PYTHONUNBUFFERED=1

# Pinned weights, verified against the digest in matting.MANIFEST before use.
RUN if [ "${IMAGEN_MATTING}" = "1" ]; then \
        imagen matting fetch "${IMAGEN_MATTING_MODEL}" --accept >/dev/null \
        && test -s "/models/${IMAGEN_MATTING_MODEL}.onnx"; \
    fi

RUN mkdir -p /jobs

EXPOSE 8000

CMD ["uvicorn", "--factory", "--app-dir", "/app", "app:from_environment", \
     "--host", "::", "--port", "8000"]
