# ADR 0001: LangGraph for the application preparation flow

Status: accepted for Phase 2 step A. Dependencies are pinned: langgraph==1.2.24, langgraph-checkpoint-postgres==3.2.1, psycopg[binary]==3.3.9.

## Context

Preparing an application is a multi-step process: read the job page, analyse it, retrieve the candidate's
facts, tailor the CV, draft the email, check every claim, and retry when a claim cannot be traced. Today this
is one service function with the steps written in sequence. The retry loop and the pause for human review are
the parts that are hardest to keep correct by hand.

## Decision

Use LangGraph to orchestrate the preparation flow, and nothing else from the LangChain ecosystem.

- Nodes call the existing services. They do not reimplement them: the LLM client (`infra/llm.py`, with its
  retry and rate limiting), the prompt templates, `safe_http`, the RAG service, the CV pipeline and the
  deterministic grounding check.
- The graph may prepare an application and pause for human review. It never approves and never sends.
  The application state machine, the content hash, the frozen PDF, the atomic claim and the send policy stay
  the single source of truth, in the services.
- Human review uses LangGraph's interrupt and resume, with a persistent Postgres checkpointer, so a pause can
  survive a restart of the API.

## Alternatives considered

- **Keep the sequential function and add a loop by hand.** Works for one loop; becomes hard to reason about
  with pauses and restarts. The checkpointer would have to be written too.
- **LangChain chains and agents.** Heavier, and it would pull integrations (Groq, search, vector stores) that
  duplicate code we already have and already test.

## Consequences

- Two new dependencies: `langgraph` and `langgraph-checkpoint-postgres` (see the dependency note in the
  Phase 2 report for sizes). The checkpointer uses psycopg v3, which coexists with the psycopg2 driver used by
  SQLAlchemy.
- The graph's tables are created by the checkpointer. They must be part of the Alembic history, not created
  at application start.
- Every LLM call goes through the same rate limiter, whatever the caller.

## Open questions (all resolved, see the decisions below)

1. Resume: should `POST /applications/{id}/approve` resume the graph's pause, or should the graph be closed
   separately? Either way, the approval itself stays in the service.
2. Checkpointer tables: generated once and stored in an Alembic revision (versioned, reviewable), or created
   by `setup()` at startup (simpler, unversioned)? This draft proposes Alembic.
3. Connection string for the checkpointer: a dedicated setting, or derived from `DATABASE_URL`?

## Decisions taken with the owner

1. **Approval is independent of the graph.** `POST /applications/{id}/approve` performs all critical work itself
   (grounding, content hash, frozen PDF). It then resumes the graph, and a failed resume only closes the workflow
   record. A test removes the checkpoint and shows that approval still succeeds.
2. **The checkpointer schema is owned by Alembic.** Migration `0006` contains the library's own statements, copied
   from the pinned version. A test fails if the library's statements change, and another shows that the library's
   `setup()` does nothing after the migrations.
3. **Connection string.** `CHECKPOINT_DATABASE_URL` is a dedicated setting. When it is empty, `DATABASE_URL` is used
   with the `+psycopg2` suffix removed. Neither is ever logged.
4. **Token budget.** One budget per process, reserved before each call and corrected with real usage. `Retry-After`
   stays as a safety net. A multi-worker deployment would need a shared budget, which is out of scope.
5. **Background runs.** A run is a database row with a visible status. A partial unique index enforces one active run
   per (user, recipient, job title). Startup recovery resumes stale runs from their checkpoint or fails them.
6. **Windows.** The asynchronous psycopg driver requires the selector event loop. It is selected at startup.

## Measured dependency cost

Installed in a throwaway environment, then pinned in the project:

| Package | Role | Installed size |
|---|---|---|
| langgraph 1.2.24 (and langgraph-core, langgraph-sdk, langchain-core) | orchestration | about 32 MB together with its dependencies |
| langgraph-checkpoint-postgres 3.2.1 | persistence of pauses | included above |
| psycopg[binary] 3.3.9 | driver for the checkpointer | about 7 MB |

Total: about 39 MB, excluding the packages the project already had. Excluded on purpose: langchain-groq,
langchain-community, langsmith.
