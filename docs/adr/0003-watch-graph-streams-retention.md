# ADR 0003: The watch graph, progress streams and retention

Status: proposed for the database part. The graph, the stream tickets, the retention purge and the deletion of an account
are implemented and tested. The two schema changes below (migrations `0010` and `0011`) and the wiring of the watch to
the database are **waiting for the owner's go**. No migration has been written or applied for them.

## Context

The watch searches the web for offers and certifications that fit a user, and keeps the ones worth a look. It is the
second background workflow, after the research agent (ADR 0002), so it reuses the same run pattern: a 202 with an
unguessable run id, status polling, a heartbeat, ownership, one active run per request, recovery at startup, and the
`kind` column of `preparation_runs`.

## Decisions

### 1. The watch is a single-pass graph

`search -> dedupe -> read -> score -> route -> persist`, with no loop. The number of model calls is therefore known before
the run starts: `worst_case_llm_calls` in `services/watch_pipeline.py`. Scoring and routing run in batches
(`WATCH_BATCH_SIZE`, default 6). Routing runs only on the candidates that reach the score threshold.

- **Queries** come from the profile and the configured templates (`WATCH_QUERY_TEMPLATES`). No result text becomes a query.
- **Dedupe** compares canonical links: lower-case scheme and host, no `www.`, no fragment, no trailing slash, no tracking
  parameter, sorted parameters. The check is against the links the user already has, and within the run.
- **Grounding**: a company or a job title returned by the model is kept only if it appears in the text the candidate was
  judged on. A justification naming a capitalised term absent from that text is blanked. This is the research
  grounding rule (ADR 0002 and the research checks), applied to the watch.
- **Snippets**: a candidate whose page was not read is stored with `unverified` set, and its score is capped
  (`WATCH_SNIPPET_SCORE_CAP`). A page refused with 403 is a snippet, not a failure.
- **Routing** is a structured classification (`job`, `certification`, `other`), never a keyword rule. `other` is dropped.
- **No action**: the graph has no dependency on the mailer, the applications or the publisher. A test checks the imports
  and the dependency fields. An offer can be handed to the application preparation by the user, never by the watch.

### 2. Progress streams with single-use tickets

A browser `EventSource` cannot send an `Authorization` header. The login token must not go into a URL, because URLs reach
logs, history and proxies. The chosen approach:

- `POST /runs/{run_id}/stream-ticket` (authenticated) issues a random ticket, valid `STREAM_TICKET_SECONDS` (default 60),
  bound to the user and to the run.
- `GET /runs/{run_id}/events?ticket=...` opens the stream. The ticket works **once**. Refused cases, each with its reason:
  `unknown`, `expired`, `reused`, `wrong_run`, and `wrong_user` (the run's owner is not the ticket's user).
- The stream carries step names and status only (an allowlist in `stream_event`). Prompts, answers, queries, page text and
  titles never enter a stream. Polling (`GET /runs/{run_id}`, and each endpoint's own status) stays available.

**Limit, accepted for now:** tickets live in the memory of one process, as the token budget does. With several API
workers, a ticket issued by one worker is unknown to the others. The fix is a shared store (a table, or Redis), decided
with the multi-worker deployment, not now.

### 3. Retention and deletion

- `RESULT_RETENTION_DAYS` (default 365): finished research and watch runs, their workflow checkpoints, and the watch
  results older than that are purged, once at startup and then every `RESULT_PURGE_INTERVAL_SECONDS`. Running runs and
  application runs are never purged by this job.
- `DELETE /auth/account` (with the password typed again) removes every row that belongs to the user, in one transaction,
  respecting the foreign keys (runs before the applications they point to), with the user's checkpoints. Other users'
  data is untouched. A wrong password removes nothing.

## The two schema changes (waiting for the go)

Both are additive: new nullable or defaulted columns, no data rewritten, and a downgrade that drops them.

**Migration `0010_profile_watch_preferences`** on `profile_config`:
- `regions` (text, JSON list, nullable), `languages` (text, JSON list, nullable), `offer_type` (string, nullable).
- Without them the watch uses the settings defaults (`WATCH_DEFAULT_OFFER_TYPE`, and the profile's `field` and `level`).

**Migration `0011_watch_result_provenance`** on `watch_results`:
- `canonical_link` (string, nullable, indexed): the dedupe key of new rows. Old rows are canonicalised in Python at read.
- `fetched` (boolean, not null, default false) and `unverified` (boolean, not null, default true). Existing rows are
  marked unverified: they were scored on the search summary.
- `company` and `job_title` (string, nullable).
- The category maps onto the existing `type` column (`job`, or `cert` for a certification). The retrieval date is the
  existing `created_at`.

The run itself needs no migration: its `kind` is `watch`, a string value of the column added by `0009`.

## Consequences

- Cost, with the defaults: at most **8 model calls** per run (2 scoring batches and 2 routing batches, one retry each).
  The count does not include HTTP 429 retries.
- Duration at 8000 tokens per minute (estimate, to check with the logs): a typical run needs about 1 to 2 minutes; the
  worst case (12 candidates, every call retried once, four pages read) is about 5 to 6 minutes. Most of it is the wait for
  the token budget.
- The synchronous `POST /watch/run` was removed before publication: it made one scoring call per result with no run record,
  and bypassed the run pattern. The asynchronous run is the only way to start a watch, once it is wired.
