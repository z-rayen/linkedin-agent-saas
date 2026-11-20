# syntax=docker/dockerfile:1
# Multi-stage build: dependencies are resolved from uv.lock in the builder stage,
# and the runtime image contains only the virtualenv, the source and the migrations.

ARG PYTHON_VERSION=3.13
ARG UV_VERSION=0.13.9

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:${PYTHON_VERSION}-slim AS builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:${PYTHON_VERSION}-slim AS runtime
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/').status == 200 else 1)"
# Apply migrations before serving. Migrations are idempotent with respect to the revision table.
CMD ["sh", "-c", "alembic upgrade head && uvicorn linkedin_agent.main:app --host 0.0.0.0 --port 8000 --app-dir src"]
