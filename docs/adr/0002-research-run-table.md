# ADR 0002: Where background agent runs are stored

Status: accepted. Option A is implemented: migration `0009_background_run_kind` (additive, with a tested downgrade that
refuses while research runs exist), the endpoints `POST /agent/research` and `GET /agent/research/{run_id}`, ownership,
unguessable ids, one active run per request, and restart recovery. The tests are in `tests/test_research_api.py` and
`tests/test_migration_0009.py`.

Two details decided during the implementation, beyond the options below:

- A finished research run has the status `done` (not `closed`), so that a research run is never mistaken for an
  application.
- The planner for research runs is `planner.v2`, which wraps the conversation history as untrusted data. The earlier
  synchronous `/agent/ask` (with `planner.v1`) was removed in the cleanup that followed this ADR.

## Context

The research agent graph (`graphs/research_graph.py`) can take minutes: several searches, page reads, reflection
rounds and LLM calls, all under the token budget of one process. Its HTTP contract must be asynchronous, like the
application preparation: `POST` returns `202` with an unguessable run id, the client polls for status, and the run has
a heartbeat, an ownership check, at most one active run per user and request, and recovery at startup.

The preparation mechanics already exist in `preparation_runs` (migration `0007`, public id `0008`), in
`services/preparations.py`, and in the startup recovery. Two things are missing for the research runs:

1. a way to tell the kinds of run apart (an application preparation must never be resumed as a research run);
2. a place for the result: the answer, its numbered sources, and the id of a drafted post.

The current columns are named for applications: `lock_key` is built from the recipient and the job title, and
`application_id` points to `applications`.

## Options

**A. Generalize `preparation_runs` (recommended).** Add two additive columns:

- `kind` (text, not null, server default `'application'`): `application` or `research`. Existing rows take the
  default, so nothing is rewritten.
- `result_json` (text, nullable): the result of a research run. Empty for applications.

For research runs, `lock_key` is the hash of `[user_id, kind, normalized request]`. The partial unique index on
active runs stays as it is, and the hash keeps the two kinds apart. Startup recovery and `_run` dispatch on `kind`.
Everything else is reused: public ids, status and heartbeat, ownership, the checkpointer thread id (still unique,
because it is the integer primary key).

- Migration `0009_background_run_kind`: two `add_column` calls. The downgrade refuses to run while research rows
  exist, instead of silently deleting them.
- Cost: the table name is historical. A rename (to `background_runs`) can follow later, as its own migration.

**B. A separate `agent_runs` table.** Same columns, its own partial unique index, and the lifecycle functions
parameterized by the model. Clearer names. The cost is a second copy of the recovery loop and the status endpoint
paths to keep correct and tested, for no behavioural gain today.

**C. Store the research result in `agent_messages` and reuse `preparation_runs` without a `kind`.** Rejected: nothing
tells a research run from an application, so recovery would try to resume an application as a research run.

## Decision

Option A. The owner confirmed it before the migration was written.

## Consequences of A

- A research answer is stored in `result_json`. It is personal data in the same sense as the content of the agent
  conversation: it stays out of logs, and it is deleted with the user. A retention rule for finished runs is a
  separate, open decision.
- The `kind` value is part of the API: the research endpoint only ever lists and resumes `research` runs.
- Tests needed with the migration: ownership and unguessable ids for research runs, one active run per user and
  request, restart recovery of a research run from its checkpoint, and that an application run is never resumed by
  the research code path.

## Related decision: who asks for a post

A research run drafts a LinkedIn post only when the request carries an explicit flag (`draft_post`), set by the
client. The planner's intent is not used to decide it. This is a choice the owner should confirm: the existing
The planner's intent is no longer used for that decision, since `POST /agent/ask` was removed.
