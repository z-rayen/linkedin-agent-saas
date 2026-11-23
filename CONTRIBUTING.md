# Contributing

Thank you for considering a contribution. This project is an assistant for a job search, so a few rules protect the
people who use it. They are not optional.

## Before you start

- Read the [README](README.md), section "Responsible use", and [the architecture notes](docs/ARCHITECTURE.md).
- For a change that is hard to undo or that trades one property against another, write a short ADR in `docs/adr/`
  (see the existing ones for the format). Decisions with real trade-offs go there, not in a pull request description.
- Open an issue first for anything larger than a fix. It saves you a rewrite.

## The rules that cannot be relaxed

- **LinkedIn**: only the official API. No browser automation, no auto-comment, no auto-connect.
- **E-mail**: nothing is sent without explicit approval in the interface. Dry-run stays the default. Recipients come from
  the job page or are typed by the user, never guessed.
- **Grounding**: CV and e-mail content may only select, reorder and rephrase facts from the master CV and the knowledge
  base. A change that lets the model invent an employer, a date, a figure or a skill is rejected.
- **Untrusted input**: job pages, search results and conversation history are data. They are wrapped as data in prompts
  and never become instructions or queries.
- **Secrets and personal data**: never commit a key, a token, a real address or a CV. `scripts/audit_tracked.py` checks
  this in the pre-commit hook and in CI. Use `example.com` (or another reserved domain) in examples and tests.

## Setting up

Follow the quickstart in the README. The backend runs on Python 3.13 with `uv`; the frontend on Node 26.

```bash
cd backend && uv sync --frozen && uv run pytest -q
cd frontend && npm ci && npm test
```

## Making a change

1. Create a branch from `main`. Keep one concern per branch.
2. Write the test first when the change fixes a bug: the test must fail before the fix.
3. Run the checks of the part you touched:
   - backend: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
   - frontend: `npm run format:check && npm run lint && npm run typecheck && npm test && npm run build`
   - generated files: `npm run check:types` after an API change (regenerate with `npm run gen:types`), and refresh the
     OpenAPI snapshot with `python scripts/dump_openapi.py --write ../frontend/openapi.json` from `backend/`.
   - secrets and personal data: `python scripts/audit_tracked.py`
4. Keep commits focused and use the Conventional Commits style (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `ci:`).
5. Open a pull request. Fill in the template. Explain what you verified and what you could not verify.

## Database changes

Schema changes are Alembic migrations only, numbered after the last one, additive where possible, with a downgrade and a
test on a populated table (see `backend/tests/test_migration_0011_0012.py` for the pattern). Never edit a migration that
has been applied anywhere.

## Language

Code, comments, documentation, commit messages and this file are in English. User-facing text of the interface is
translated in both `frontend/src/i18n/fr.ts` and `en.ts`; a test checks that the two catalogues have the same keys.

## Reporting a security problem

Do not open a public issue. Follow [SECURITY.md](SECURITY.md).
