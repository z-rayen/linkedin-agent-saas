# Common tasks. Windows users without make: use scripts/dev.ps1 (same task names).
# Requires uv (https://docs.astral.sh/uv/) and Docker for the database.

BACKEND := backend
UV := uv --directory $(BACKEND)

.PHONY: help install db-up db-down migrate run test test-integration cov lint format typecheck check docker-up docker-down

help:
	@echo "install     install backend dependencies from uv.lock"
	@echo "db-up       start PostgreSQL (pgvector) with docker compose"
	@echo "db-down     stop the database containers"
	@echo "migrate     apply Alembic migrations to the dev database"
	@echo "run         start the API with reload on http://127.0.0.1:8000"
	@echo "test        run the fast test suite (needs the test database, see backend/tests/conftest.py)"
	@echo "test-integration run the Postgres integration tests (checkpointer, restart)"
	@echo "cov         run tests with the coverage gate"
	@echo "lint        ruff lint and format check"
	@echo "format      ruff format and autofix"
	@echo "typecheck   mypy"
	@echo "check       lint + typecheck + cov (what CI runs)"
	@echo "docker-up   build and start database + API"
	@echo "docker-down stop the full stack"

install:
	$(UV) sync --frozen

db-up:
	docker compose up -d db

db-down:
	docker compose down

migrate:
	$(UV) run alembic upgrade head

run:
	$(UV) run uvicorn linkedin_agent.main:app --reload --app-dir src

test:
	$(UV) run pytest -q

test-integration:
	$(UV) run pytest -q -m integration

cov:
	$(UV) run pytest --cov

lint:
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format:
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

typecheck:
	$(UV) run mypy

check: lint typecheck cov

docker-up:
	docker compose up -d --build

docker-down:
	docker compose down
