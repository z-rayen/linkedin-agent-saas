# ADR 0005: The watch as a background run, its results and their retention

Status: accepted. Implemented in `services/watch_runs.py`, `routers/watch.py`, `services/watch_pipeline.py`,
`graphs/watch_graph.py`, with migrations `0011_profile_watch_preferences` and `0012_watch_result_provenance`.

## Context

ADR 0003 left the watch as a graph with its database part waiting for a decision. The graph existed; it had no run record,
no status, and the synchronous `POST /watch/run` made one model call per result with no way to follow it. A watch run
takes minutes and calls a rate-limited model, so it must follow the same discipline as the research runs.

## Decisions

1. **A watch is a background run of kind `watch`** in the table of runs (`preparation_runs`). It has the same lifecycle as
   the research runs: `queued -> running -> done | failed`, a heartbeat, a stale check, resumption from the checkpoint at
   startup, and an unguessable public id (192 random bits). The run id is never a sequential number.
2. **One active watch per account**, enforced by the database: the lock key is a hash of the user and the kind, and a
   partial unique index refuses a second active run. Another account is not affected.
3. **Preferences are read once, when the run starts**: regions, languages and offer type from the profile, with the
   settings defaults for every missing value. Nothing from a search result becomes a query.
4. **Provenance on every result**: the canonical link (the dedupe key), `fetched` (the full page was read), `unverified`
   (judged on the snippet only), and the company and job title, each kept only when the text the result was judged on
   contains them. Rows written before migration 0012 are `fetched = false, unverified = true`: they were judged on a
   snippet, so they are shown as such. Their canonical link is computed when they are read, so dedupe covers them too.
5. **Retention.** Finished watch runs and watch results older than `RESULT_RETENTION_DAYS` (365) are purged by the existing
   retention job, once at startup and then every `RESULT_PURGE_INTERVAL_SECONDS`. Application runs and applications are
   never purged by this job. Account deletion removes the user's watch runs, results, profile and checkpoints in one
   transaction (`services/retention.py`, tested).
6. **No path to send, approve or publish.** The graph and the run service do not import the mailer, the application
   services, the preparation service or the publishing router. A test checks the imports of the four watch modules, and a
   runtime test replaces the sending and approval functions with failures and runs a whole watch.
7. **Ambiguities taken in the conservative direction**: a page that cannot be read does not fail the run; a result the
   model cannot place is dropped; an unknown category is "other" and is dropped; a failed search query does not stop the
   others. A failed run stores nothing partial: results are written by the persist step only.

## Migration numbering

ADR 0003 reserved `0010` and `0011` for the watch. `0010` was taken by the email normalisation (see the migration's own
docstring). The watch uses `0011` (profile preferences) and `0012` (result provenance). ADR 0003's numbering is superseded
by this note.

## Consequences

- Cost with the defaults: at most 8 model calls per run (2 scoring batches and 2 routing batches, one retry each).
  Duration: typically 1 to 2 minutes at 8000 tokens per minute; the worst case about 5 to 6 minutes, mostly waiting for the
  token budget. Both figures are estimates to check against the `watch node=... duration_ms=...` logs.
- No new dependency. The run pattern is the one already tested for the research runs.
- Known limitation: the in-memory checkpointer and the progress tickets live in one process, as in ADR 0003.
- The company and the job title are compared as whole strings, case and accents aside, so a lowercase company is checked
  like any other. Company names found only inside a longer sentence of the page are not extracted: the model must name them.
