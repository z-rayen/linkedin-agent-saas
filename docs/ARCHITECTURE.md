# Architecture
Guidance for AI assistants and contributors working in this repository.

## Project

LinkedIn Agent: a self-hosted assistant for a job seeker / early-career ML engineer.
It searches job and certification opportunities, scores them against a profile,
drafts cover letters, tailored CV sections and application emails, generates
LinkedIn posts and comment suggestions, and publishes posts through the official
LinkedIn API.

Target evolution (see "Roadmap"): job-application emails with a tailored PDF CV
(always after human approval), LangGraph agent graphs, a React frontend, and a
senior-grade, open-source-ready codebase.

## Current state (as of this file)

- Prototype. Not a git repository yet. No tests, no lint config, no `.gitignore`,
  no `requirements.txt`, no migrations.
- Backend: `backend/src/linkedin_agent/` (FastAPI, SQLAlchemy, PostgreSQL + pgvector).
  Package moved from `backend/app/` without changes to its code (step 4a). Tests in `backend/tests/`.
  Routers: `auth`, `oauth`, `content`, `watch`, `profile`, `agent`, `engage`, `apply`.
  Services: `groq_service`, `watch_service`, `search_service`, `agent_service`,
  `engage_service`, `apply_service`, `rag_service`.
- Frontend: `frontend/` plain HTML, CSS and vanilla JavaScript (`app.js`), served as static files.
- Infra: `docker-compose.yml` runs `pgvector/pgvector:pg16`.
- Python 3.13 in local use (`__pycache__` is `cpython-313`).

## Stack

- Python 3.13, FastAPI, Pydantic, SQLAlchemy 2.x style (currently 1.x-style queries),
  PostgreSQL 16 + pgvector.
- Auth: JWT (python-jose, HS256), bcrypt via passlib, HTTPBearer.
- LLM: Groq, OpenAI-compatible HTTP, model `openai/gpt-oss-20b`. Free tier is about
  8000 tokens/minute, so calls must be throttled.
- Web search: Serper.dev. Page reading: httpx + BeautifulSoup4. RSS: feedparser.
- Embeddings: fastembed, `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
  (384 dims), stored in pgvector `Vector(384)`.
- External APIs: LinkedIn OAuth 2.0 + UGC Posts API, GitHub OAuth + repos API.

## Run (current prototype, Windows PowerShell)

```powershell
# 1. Database
docker compose up -d db

# 2. Backend (from backend/, env vars in backend/.env)
#    DATABASE_URL, SECRET_KEY, GROQ_API_KEY, SERPER_API_KEY,
#    LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET, LINKEDIN_REDIRECT_URI,
#    GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, GITHUB_REDIRECT_URI, FRONTEND_ORIGIN
.\.venv\Scripts\python.exe -m uvicorn linkedin_agent.main:app --reload --app-dir src

# Tests (needs the test database on port 5433, see backend/tests/conftest.py)
.\.venv\Scripts\python.exe -m pytest -q

# 3. Frontend: open frontend/index.html or serve the folder with any static server.
```

The schema is managed by Alembic only (`backend/migrations/`). Run `make migrate`
(or `.\scripts\dev.ps1 migrate`) before the first start. A database created by the
prototype's `create_all` must be marked as current once, not migrated:
`uv run alembic stamp 0001_baseline`.

Other commands: `make check` (lint, mypy, tests with coverage gate). On Windows without
make, use `.\scripts\dev.ps1 check`.

## Conventions (target, to be enforced by the refactor)

- Layout (target): `backend/src/linkedin_agent/` with `api/`, `core/`, `domain/`,
  `services/`, `infra/`, `graphs/`, `prompts/`, `cv/`; tests in `backend/tests/`;
  config in `config/defaults.yaml`; frontend in `frontend/`; docs in `docs/`.
- Dependency direction: `api -> services -> domain`. Infra sits behind interfaces
  (Protocol/ABC). Routers contain no business logic.
- No magic numbers or strings in logic. URLs, models, thresholds, caps, feeds,
  query templates and timeouts come from typed settings (env) or `config/defaults.yaml`,
  with per-user overrides in the DB.
- Prompts are versioned template files in `prompts/`. LLM outputs are parsed into
  Pydantic models with bounded retry. No string slicing for JSON.
- Alembic migrations only. SQLAlchemy 2.0 style. Repositories. Pydantic schemas
  separate from ORM models.
- Python 3.11+, full type hints, mypy, ruff (lint + format). No `print()`: use structured
  logging with request IDs and secret redaction.
- Errors: custom exception hierarchy mapped to HTTP responses by global handlers.
  httpx clients have timeouts and retries with backoff (tenacity).
- Frontend (later): Vite + React + TypeScript strict, ESLint + Prettier, Vitest +
  Testing Library, API URL via Vite env, i18n strings FR/EN.
- Tests: pytest with mocked external services; coverage threshold enforced in CI.
- Decisions with real trade-offs go in `docs/adr/`.
- Commits: Conventional Commits, local only. Never `git push`, never add or change
  remotes, never create or publish a repository, never force-push.

## Safety rules (non-negotiable)

- LinkedIn: only the official API. No Selenium, Playwright, auto-comment or auto-connect.
  Engagement is assisted: suggest, the user copies.
- Email: nothing is sent without explicit approval in the UI. Recipients come only from
  the job page or are typed by the user, never guessed. Dry-run ON by default, daily
  send cap, minimum delay between sends, duplicate-send protection per recipient/company.
- Grounding: CV and email content may only select, reorder and rephrase facts present in
  the master CV and the knowledge base. Never invent employers, degrees, dates, numbers
  or skills. A verification step flags untraceable claims.
- Secrets: never log or commit them. OAuth tokens and SMTP credentials are encrypted at
  rest (Fernet). Before each commit, scan for secrets and personal data; stop and report if found.
- Personal data: real CVs, the personal knowledge base and `.env` are gitignored.
  Only anonymized samples are committed.

## Known issues in the prototype (do not copy)

- `oauth.py`: OAuth `state` is the raw `user_id`, and `/oauth/*/login` is unauthenticated
  (account-linking CSRF). `linkedin_login` prints client ID and redirect URI (`DEBUG`).
- `main.py`: CORS `allow_origins=["*"]` with `allow_credentials=True`.
- OAuth tokens are stored in plaintext. No expiry check and no refresh flow.
- `watch.py` ignores the feed URLs in `ProfileConfig` and `fetch_feed_items` is unused (dead code).
  It runs one LLM scoring call per search hit, which quickly exhausts the Groq TPM budget.
- Default profile values are duplicated in `profile.py`, `agent.py` and `watch.py`.
- `_call_groq` (private, in `watch_service.py`) is imported by `agent_service`,
  `engage_service` and `apply_service`. Duplicate Groq client code exists in `groq_service.py`.
- Prompts are inline in Python. JSON is parsed with `removeprefix/removesuffix`.
- `content.py` has duplicate `import httpx` lines. `schemas.py` and routers use mixed styles.
- No `.gitignore` while `backend/.env` exists.

## Roadmap (see the project brief for full detail)

- Phase 0: foundations (layout, settings, logging, exceptions, Alembic, pinned deps,
  pre-commit, CI skeleton, Makefile, Dockerfile, characterization tests, provider
  interfaces, prompt templates, security fixes).
- Phase 1: applications table, tailored PDF CV pipeline, SMTP sending behind a Mailer
  interface, approve/send UI flow.
- Phase 2: LangGraph graphs (application, research agent, watch) with a shared rate limiter.
- Phase 3: React frontend with parity to the current features.
- Phase 4: docs, full compose, green CI, pre-publication checklist. Nothing is published
  without the owner's approval.

## Working agreement

- One phase at a time. After each phase: run tests, linters and type checks, summarize,
  give exact commands to try, then stop for approval.
- Characterization tests before refactoring. Keep endpoints working until their consumers are replaced.
- Replies to the owner are in French. Code, comments, docs and commit messages are in English.
