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

COPY deploy/worker/app.py /app/app.py

RUN mkdir -p /jobs

ENV IMAGEN_JOBS_ROOT=/jobs \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "--factory", "--app-dir", "/app", "app:from_environment", \
     "--host", "::", "--port", "8000"]
