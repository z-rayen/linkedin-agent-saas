# syntax=docker/dockerfile:1
# Multi-stage build: dependencies are resolved from uv.lock in the builder stage,
# and the runtime image contains only the virtualenv, the source, the migrations and the pinned Tectonic binary.

ARG UV_VERSION=0.13.9

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# Base images are pinned by digest (python:3.13-slim). Dependabot proposes the newer digests.
FROM python@sha256:46837ed3105db0029b6d656816185c6258e3b39690532fb782428e0a5fd76182 AS builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python@sha256:46837ed3105db0029b6d656816185c6258e3b39690532fb782428e0a5fd76182 AS runtime
ARG TARGETARCH
# Tectonic compiles the tailored CVs. The release is pinned, and its download must match the SHA-256 that GitHub
# publishes for the asset (checked by docker/install_tectonic.py). The digests below are for version 0.17.0.
ARG TECTONIC_VERSION=0.17.0
ARG TECTONIC_SHA256_AMD64=8533d07f9ccbd7a65824b9e0459041bca34af1eb33daba48f59215593753a3b7
ARG TECTONIC_SHA256_ARM64=b10954a95404f3ab2328d2fa59a5ebab8e657f893fab096f98be8db7c0c979b8
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TECTONIC_PATH=/usr/local/bin/tectonic
COPY docker/install_tectonic.py /tmp/install_tectonic.py
RUN python /tmp/install_tectonic.py && rm /tmp/install_tectonic.py
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
# The embedding model cache is written at runtime (FASTEMBED_CACHE_DIR, default data/models/fastembed).
RUN mkdir -p /app/data/models && chown -R app:app /app/data
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/').status == 200 else 1)"
# Apply migrations before serving. Migrations are idempotent with respect to the revision table.
# The API refuses to start if Tectonic is missing or broken (TECTONIC_REQUIRED, default true).
CMD ["sh", "-c", "alembic upgrade head && uvicorn linkedin_agent.main:app --host 0.0.0.0 --port 8000 --app-dir src"]
