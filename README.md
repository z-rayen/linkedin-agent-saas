# LinkedIn Agent

A self-hosted assistant for job seekers: it finds opportunities, tailors a CV to each one, drafts the
application email, and sends it only after you approve it. Everything is grounded: a claim in the CV or the
email must trace back to a fact in your master CV.

Status: prototype in active development (see `docs/ARCHITECTURE.md` for the architecture and the roadmap).

## Configuration: two .env files

| File | Read by | Contains |
|---|---|---|
| `.env` at the repository root (from `.env.example`) | `docker compose` only | `POSTGRES_PASSWORD` |
| `backend/.env` (from `backend/.env.example`) | the API, when run from `backend/` | everything else: database, secrets, LLM, mail, Tectonic |

Both files are gitignored. The test suite never reads either one: it builds its own settings with fake values.
When the API runs inside Docker, the `api` service takes its `backend/.env` and overrides only `DATABASE_URL`.

## Where your data goes

Read this before you connect any account. Some data leaves your machine, to the services below.

| Service | What is sent | When |
|---|---|---|
| **LLM provider** (Groq by default) | Your master CV facts, your profile fields, knowledge-base entries you added, job descriptions, the text of job pages and web sources, and the prompts that combine them | Every CV tailoring, email draft, post generation, comment suggestion, watch scoring and research answer |
| Serper.dev | Search queries built from your profile or your questions | Watch, research agent, engagement suggestions |
| Job pages and web sources | Plain HTTP requests to the URLs found by search | Application preparation, research agent |
| LinkedIn API | Your posts, with your OAuth token | Only when you press publish |
| GitHub API | Repository list, with your OAuth token | Only when you open the repository list |
| Your SMTP server | The approved email, and the approved CV PDF as an attachment | Only when you send, after approval and confirmation |

The LLM provider sees the full text of your CV facts. Besides what you entered yourself (CV, profile, knowledge
base), it also receives the job descriptions and web pages the agent fetched. Choose a provider whose data
policy you accept.

### Switching LLM provider

The LLM is selected in `backend/src/linkedin_agent/infra/llm.py` (`get_llm_client`). Two ways:

1. **Any OpenAI-compatible chat endpoint** (Groq, OpenAI, a local Ollama or vLLM server...): change three
   settings in `backend/.env`. The variable names still say `GROQ_` for historical reasons:
   ```
   GROQ_URL=http://localhost:11434/v1/chat/completions   # example: Ollama
   GROQ_MODEL=llama3.1
   GROQ_API_KEY=anything-if-the-server-ignores-it
   ```
2. **A different protocol**: write a class with `async def complete(self, prompt: str) -> str` that raises
   `LLMUnavailableError` when it cannot answer, and return it from `get_llm_client`. Every caller goes through
   that protocol, so nothing else changes. Tests replace the client with a fake; see `tests/test_llm_client.py`.

Search (`SERPER_API_KEY`) and embeddings (a local model, no data leaves the machine) are switched the same way,
through `infra/search.py` and `infra/embeddings.py`.

## Application preparation (LangGraph)

Preparing an application runs in the background as a LangGraph workflow. Its diagram is generated from the code:
[docs/graphs/application_graph.md](docs/graphs/application_graph.md) (regenerate with `scripts/export_graph_mermaid.py`).

**What the model writes, and what the code adds.** The model writes the email body and selects, orders and rephrases
the skills, experience and projects. The code adds the rest:

- the **header** (name, headline, contact line) and the **education**, from the master CV. A master CV without a
  headline, a contact email or an education fact cannot be approved (kind `mandatory`);
- the **signature** of the email (name and contact line), appended to the body. The model never writes a closing, a
  signature or a placeholder such as `[Your Name]`; a placeholder left in the text is a problem of kind `placeholder`.

**Grounding problems** are returned as a structured list, never truncated: each one has a `kind` (`number`, `term`,
`technology`, `claim`, `placeholder`, `unknown_source`, `mandatory`), a `location` (`line 3`, `email body`,
`master CV`), the offending `term`, and a `message`. The approval refusal (409, `cv_not_grounded`) carries the complete
list in `problems`.

**Figures** are compared by value: `2,000`, `2000` and `2 000` are the same number; `3` and `30` are not. **Words**: the
neutral words of a letter (Your, Dear, Best, Regards, Thank, and their French equivalents) assert nothing; level and
scope words (proficient, expert, advanced, led, managed, fluent...) are claims that the master CV must support.
**Typography**: no-break and narrow spaces, non-breaking hyphens and curly quotes are compared and rendered as their
plain equivalents. A compile that reports a missing glyph is refused, so the PDF never has a blank where a character
should be.

**Revisions.** When the revision budget is used up with problems left, the preparation status says so
(`grounding.revision_limit_reached`), and the application stays in review with its problems listed.

### The asynchronous flow

Preparation takes minutes, so the API does not wait for it. Every step is a separate request:

1. `POST /applications` with the job (text or URL), the recipient and the job title. It returns **`202 Accepted`**
   at once, with a `run_id` and `status: "queued"`. Nothing is stored as an application yet.
2. Poll `GET /applications/preparations/{run_id}` every few seconds. Stop when `status` is `awaiting_review`
   (read `application_id`, then `GET /applications/{application_id}`), `closed`, or `failed` (read `error`).
3. Review the application, then approve and send through their own endpoints. Preparing never approves or sends.

Run statuses: `queued`, `running`, `awaiting_review`, `closed`, `failed`. `step` names the graph node in progress.
`stalled: true` means the run has sent no heartbeat for `PREPARATION_STALE_SECONDS`.

Example, with `curl` and a token in `$TOKEN`:

```bash
RUN=$(curl -s -X POST http://localhost:8000/applications \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"job_title":"ML Intern","job_text":"...","recipient_email":"hr@acme.com"}' | jq -r .run_id)
STATUS_URL=http://localhost:8000/applications/preparations/$RUN
until curl -s "$STATUS_URL" -H "Authorization: Bearer $TOKEN" \
  | jq -e '.status == "awaiting_review" or .status == "closed" or .status == "failed"' > /dev/null; do sleep 3; done
curl -s "$STATUS_URL" -H "Authorization: Bearer $TOKEN"
```

The interactive API documentation (`/docs`) describes each endpoint, including these statuses.

### Run ids, ownership and concurrency

- `run_id` is a random 192-bit token, not a database number. Only the user who started the run can read it. For
  anyone else, and for an unknown token, the answer is the same `404`.
- One active run per user, recipient and job title, enforced by the database. A blank recipient or job title is
  refused (`400`) rather than locking unrelated applications together.

### Heartbeat, stalled runs and restarts

- A running preparation sends a heartbeat at every graph node and during every wait: the shared LLM token budget and the
  provider's `Retry-After` pause (chunked by `HEARTBEAT_INTERVAL_SECONDS`, default 10 s).
- A run is **stalled** when its heartbeat is older than `PREPARATION_STALE_SECONDS` (default 300 s). The application
  refuses to start if that value is not longer than the slowest external call plus the heartbeat interval (40 s with
  the defaults), so a healthy run cannot be reported as stalled.
- At startup, stale runs are resumed from their checkpoint, or marked `failed` with an instruction when no checkpoint
  exists. None stays stuck.

### Workflow checkpoints

- The checkpointer is one connection pool, opened at startup and closed at shutdown (`CHECKPOINT_POOL_MAX_SIZE`).
  No run opens its own connection.
- `CHECKPOINT_BACKEND=postgres` (default) survives restarts. `memory` is for tests only: a restart then fails the
  active runs that had no checkpoint.

- The graph pauses for review and never approves or sends. Approval is the existing endpoint, which performs the
  grounding check, the content hash and the frozen PDF itself. Resuming the graph afterwards only closes its record.

**Worst-case duration.** The graph allows `GRAPH_MAX_REVISIONS=2` revisions. At most 14 LLM calls are made: analysis
(2 with one validation retry), first draft (CV 2, email 2), and two revisions (CV 2 and email 2 each). The research
step uses search only, not the LLM. At the default budget of 8000 tokens per minute, with about 2000 tokens per call,
the worst case takes about 5 to 7 minutes and a typical run about 1 to 2 minutes. These are estimates from the
prompt sizes: check them with the logs (`graph node=... duration_ms=...`) on your own data.

**Token budget.** `LLM_TOKENS_PER_MINUTE` is shared by every call of one process. Each call reserves an estimate,
then corrects it with the usage the provider reports. A call that does not fit waits. A `429` response is a safety
net: its `Retry-After` header is honoured, capped at 60 seconds. **The budget is per process**: run one API worker,
or several workers would together exceed the provider limit.

**Output limit and reasoning tokens.** The default model (`openai/gpt-oss-20b`) is a reasoning model: the tokens it
spends thinking count against `LLM_MAX_OUTPUT_TOKENS`, together with the answer. When the cap runs out first, the
answer is cut off and is not valid JSON. The log then shows `llm answer cut step=... max_tokens=...`.

- A cut-off answer is **not** sent back with a "fix these problems" correction: the problem is the budget, not the
  answer. The call is retried once with the cap multiplied by `LLM_OUTPUT_BUDGET_MULTIPLIER` (default 2), never above
  `LLM_OUTPUT_TOKEN_CEILING` (default 4096). The retry uses one of the call's attempts (`retries`).
- If the answer is still cut off at the ceiling, the run fails with: *"The model's answer was cut off or malformed;
  retry, or raise the output limit."* Raise `LLM_OUTPUT_TOKEN_CEILING` or `LLM_MAX_OUTPUT_TOKENS`, or lower the reasoning
  effort (below).
- Each call reserves the estimated prompt tokens plus the cap from `LLM_TOKENS_PER_MINUTE`, then corrects the reservation
  with the real usage. A larger cap therefore holds more of the minute's budget while the call is in flight, so calls
  wait longer. The reservation of one call must fit the whole budget: the settings refuse a ceiling above
  `LLM_TOKENS_PER_MINUTE`, and a call whose prompt plus cap cannot fit is refused at once, with a message that names the
  variables to change, instead of waiting forever.

**Reasoning effort and per-step models.** `LLM_REASONING_EFFORT` (`low`, `medium` or `high`) is sent to the provider
when set; empty means not sent. Groq documents this parameter for `gpt-oss-20b`. Lower effort means fewer reasoning
tokens for the same answer. `LLM_STEP_OVERRIDES` sets a model and an effort per step, as JSON keyed by step name:
`cv_tailor` (the tailored CV), `analyze_job` (the job analysis), and `default` for every other call. An empty field
uses the global value. Example:

```
LLM_STEP_OVERRIDES={"cv_tailor":{"reasoning_effort":"low"}}
```

Recommendation: before changing the model, first try `reasoning_effort=low` for `cv_tailor` and measure the result on your
own master CV. The model is a validated choice (its answers are checked by the grounding rules), so a change of model
needs the same measurement. A non-reasoning model removes reasoning tokens altogether, but its output quality must be
checked the same way.

## Research agent (LangGraph)

A research run answers a question from the web, with numbered citations. The graph lives in
`backend/src/linkedin_agent/graphs/research_graph.py`; its diagram is `docs/graphs/research_graph.md`.

```
plan -> search -> read -> reflect -> search        (reflection asks for more evidence, at most RESEARCH_MAX_LOOPS times)
                                  -> synthesize -> draft_post -> end   (draft_post only when the request asks for a post)
```

- **plan** turns the request, the profile and the recent conversation into 1 to `RESEARCH_MAX_QUERIES` queries.
- **search** runs the queries. Each round keeps only new links, up to `RESEARCH_MAX_SOURCES`.
- **read** fetches at most `RESEARCH_MAX_PAGES` pages in full, each cut to `RESEARCH_PAGE_MAX_CHARS`, through
  `safe_http` (public addresses only, size and content-type caps). A refused or unreachable page keeps its search
  snippet: it never fails the run.
- **reflect** judges whether the evidence is enough. If not, it proposes new queries, and the run searches again.
  A round that finds no new link stops the loop: repeating a search cannot help.
- **synthesize** writes the answer from the sources that fit in `RESEARCH_CONTEXT_MAX_CHARS`. Only those sources are
  shown, and only their numbers may be cited. Every `[n]` is checked by code: a number that names no shown source is
  removed and counted, never kept. The answer is written in the language of the request, not of the sources.
- **draft_post** runs only when the request explicitly asks for a post. It saves a **draft** in the content queue.
  Nothing is published by the graph: publishing is a separate, manual endpoint, and no path of the graph reaches it
  (a test checks this).

Search results and page text are untrusted data, wrapped in UNTRUSTED markers in every prompt. The graph never takes a
query or a link from them: queries come from the planner and the reflection, and only links returned by the search
are read.

**Limits (all in settings, none hardcoded).**

| Setting | Default | What it bounds |
|---|---|---|
| `RESEARCH_MAX_LOOPS` | 2 | refinements after the first search round (rounds in total: this + 1) |
| `RESEARCH_MAX_QUERIES` | 3 | queries per plan or reflection |
| `RESEARCH_RESULTS_PER_QUERY` | 5 | results kept per query |
| `RESEARCH_MAX_SOURCES` | 8 | distinct links over the whole run |
| `RESEARCH_MAX_PAGES` | 4 | pages fetched in full over the whole run |
| `RESEARCH_PAGE_MAX_CHARS` | 1500 | characters kept per fetched page |
| `RESEARCH_CONTEXT_MAX_CHARS` | 9000 | characters of sources in one prompt (about 3000 estimated tokens) |
| `RESEARCH_STRUCTURED_RETRIES` | 1 | retries of the plan and reflection steps when their JSON does not validate |
| `RESEARCH_MIN_RELEVANCE` | 0.3 | share of the query's topic words a result must match (title, summary, domain) to be read and shown |
| `RESEARCH_MIN_TITLE_MATCHES` | 2 | topic words that must also appear in the title or the domain (capped by the query's topic-word count) |
| `RESEARCH_REQUIRE_CITATIONS` | true | a sentence without a citation is dropped (table rows and headings are kept) |
| `RESEARCH_DEFAULT_LANGUAGE` | "en" | the answer's language when the request's words do not reveal one |
| `RESEARCH_LANGUAGES` | English and French profiles | per language: stopwords (detection), decimal and thousands separators, and the fixed wording |

**Languages.** The language of the answer is the language of the request, detected from its words (the stopwords of
each profile). Each profile holds the labels the checks add: the snippet label, the retrieval line, the "not specified"
wording, the notes on removed statements, and the answer used when nothing could be checked. `RESEARCH_LANGUAGES` is a
JSON object; setting it replaces the whole table, so copy the defaults and add a language to extend it.

**Numbers depend on the language.** In English `2.500` is 2.5 and `2,500` is 2500; in French `2,500` is 2.5 and `2.500`
is 2500. A figure is checked with the convention of the answer's language, so a figure read with the other convention
does not match its source. The source text is read with that same convention: a source that writes a number in another
convention will not match, and that is deliberate.

**Answer checks.** The model writes the answer; code then checks it (`graphs/research_checks.py`, no model involved):

- **Traceability.** Every statement must cite a source. A statement that rests only on search snippets is labelled
  with `RESEARCH_SNIPPET_LABEL`, so it is never presented as verified. A statement with at least one full page is not
  labelled.
- **Numbers and dates, by value.** A number in a statement must appear, by value, in the text of a source it cites
  (read with the answer language's convention, see above). An ISO date is one unit. A statement with an unsupported
  number is dropped.
- **Where a number is found.** A number that appears in a full page is supported by that page. A number that appears
  only in a search snippet makes its statement snippet-based and labelled, even when another cited source was read in
  full: the figure itself is what must be verified.
- **Relative time.** "last two weeks", "recently", "current", "latest", "il y a", "derniers jours" and similar are
  dropped: their date cannot be checked against the sources. The answer says how many were removed, and that this does
  not mean nothing was found. If every statement is removed, the answer says that no statement could be checked, and
  that this does not mean nothing was found either. The counts are in `unsupported.relative_time`.
- **Entities.** Every capitalised term of a statement (a company, a job title, an acronym) must appear, accent- and
  case-insensitively, in the text of a source the statement cites: its title, its snippet or its fetched body. A term
  found only in a snippet labels the statement. A table cell naming a term that no cited source contains gets the
  "not specified" wording, and its citation is kept; a prose sentence with such a term is removed. Both are counted in
  `unsupported.entities_not_found`. Limits: sentence openers and common words are listed (`ENTITY_COMMON` in
  `graphs/research_checks.py`), so an unlisted opener is checked like a name; a name written differently in the source
  (an abbreviation, another script) is removed. The grounding reuses the CV check's capitalised-term rule.
- **Citation-only lines** ("- [1]") assert nothing: they are dropped from the answer and from `claims`.
- **Counts from several pages.** When figures come from two or more search pages, the answer says, in its language,
  that such counts are not comparable and must not be added up. This is a deliberate, conservative caution.
- **Labels and tables.** The snippet label is added by code only, from the basis: a label the model writes is removed
  first, so a full-page statement never carries one. In a table, the label and the citations stay inside the cells: a
  citation written after the last column moves into the last cell, a short row is padded, and every row keeps the
  table's width.
- **Full pages first.** Full pages are shown to the model before snippets, so a page that states a precise figure or a
  job title is used rather than ignored.
- **Empty cells.** An empty table cell is written as the language's "not specified" wording. The prompt asks the model
  for the same wording instead of a guess, and a test runs a model that leaves a field out.
- **Relevance.** A search result is read and shown only when it matches at least `RESEARCH_MIN_RELEVANCE` of the
  query's topic words, and at least `RESEARCH_MIN_TITLE_MATCHES` of them in its title or domain (capped by the number
  of topic words in the query). Words shorter than three letters are not topic words.
  *Limits*: the comparison is on words, not meanings. Acronyms of three or more letters ("NLP", "RAG", "SQL") match
  unrelated pages with the same letters, and a query made only of acronyms can keep unrelated results; a two-letter
  term ("ML") is ignored, so a query made only of such terms keeps every result.

Each run returns the answer and its traceability, in the result:

- `answer`, `retrieved_at` (the date the sources were retrieved), `language` (of the answer), `claims` (each statement,
  with `sources` and `basis` `fetched` or `snippet`);
- `sources`: only the sources the answer cites, with their `fetched` flag, so a UI can show which statements rest on a
  full page;
- `also_found`: the other retrieved sources, which no statement rests on. It is kept visible on purpose;
- `unsupported`: counts of the statements dropped, by reason: `uncited`, `relative_time`, `numbers_not_found` (a cited
  full page lacks the figure), `entities_not_found` (a name or title that no cited source contains), and
  `numbers_snippet_only` (the cited sources are snippets, and the figure is not in
  them), plus `off_topic` (search results dropped by the relevance filter)
  (search results dropped by the relevance filter).

Per step, `LLM_STEP_OVERRIDES` can set a model and a reasoning effort for `research_plan`, `research_reflect`,
`research_answer` and `research_post`, as for the other steps.

**Cost, with the defaults.** A run makes at most **12 LLM calls** with a post (10 without): plan 2, reflection 6 (three
rounds, each with one retry), answer 2 and post 2 (one cut-off retry each, see the output limit above). The count does
not include the provider's HTTP 429 retries, which are bounded by `GROQ_MAX_RETRIES`. The number is computed from the
settings by `worst_case_llm_calls()`, so it follows the configuration.

**Duration, at 8000 tokens per minute (estimate, to be checked with the logs).** Much of the time is likely spent waiting
for the token budget. A typical run (one search round, two pages, no retry) needs about 6000 to 8000 tokens: about one to
two minutes. The worst case (three rounds, every answer cut off once, a post) needs roughly 25000 to 30000 tokens, so
about four minutes of budget, plus provider latency and up to four page fetches of at most 20 seconds each: six to
seven minutes. Each node logs its duration (`research node=... duration_ms=...`), which is the way to check these figures
on your own data.

**HTTP endpoints (asynchronous).**

- `POST /agent/research` with `{"request": "...", "draft_post": false}` returns `202` with a `run_id` and
  `status: "queued"`. A run takes minutes: poll `GET /agent/research/{run_id}` until `status` is `done` (read `result`)
  or `failed` (read `error`). A heartbeat is sent every `HEARTBEAT_INTERVAL_SECONDS` for as long as the run is working,
  including during a long model call; `stalled: true` means the heartbeat stopped, and the run is resumed or failed at
  restart. Responses are UTF-8 JSON (`application/json; charset=utf-8`), accents included.
- The run id is random (192 bits). Only the user who started the run can read it; anyone else, and an unknown id, get
  the same `404`. A second run of the same request (case and spacing aside) while the first is active returns `409`.
- `draft_post: true` saves a LinkedIn post as a **draft** in the content queue. Nothing is published by the run.
- Restart: a run whose heartbeat stopped is resumed from its checkpoint when one exists, without repeating the steps
  already done. Without a checkpoint it is marked `failed`, with an instruction to send the request again.
- The result is stored in the database (`preparation_runs.result_json`). A retention rule for finished runs, and the
  deletion of a user's runs with the user, are not implemented yet. Research runs share
  the table of application preparations, with `kind = 'research'`; applications never see them, and the reverse.

The earlier synchronous `POST /agent/ask` and its history endpoints were removed: research is the only agent endpoint.

**Migration 0009** adds `kind` and `result_json` to `preparation_runs`. It is additive: existing rows are applications
and keep every value. Its downgrade refuses to run while research runs exist, because dropping `kind` would turn them
into application runs. Delete them first if you really want the downgrade. See `docs/adr/0002-research-run-table.md`.

## Watch: background search for offers and certifications

The watch graph (`backend/src/linkedin_agent/graphs/watch_graph.py`, diagram `docs/graphs/watch_graph.md`) searches the web
from your profile, scores what it finds, and keeps what is worth a look. It is one pass:
`search -> dedupe -> read -> score -> route -> persist`. It never submits an application and never sends a message:
results are a list you review. An offer can be handed to the application preparation by you, never by the watch.

- **Queries** come from the profile and `WATCH_QUERY_TEMPLATES`; no result text becomes a query.
- **Dedupe** by canonical link (no `www.`, no fragment, no tracking parameter, sorted parameters).
- **Scoring** reads the text of at most `WATCH_MAX_PAGES` pages, `WATCH_PAGE_MAX_CHARS` characters each. A result whose page
  could not be read (a 403, a timeout) is stored with a visible **unverified** flag and its score capped at
  `WATCH_SNIPPET_SCORE_CAP`. A company or title the model names must be in the text it was judged on, or it is blank.
- **Routing** classifies each result as a job, a certification or other; other is dropped. Results below
  `MIN_MATCH_SCORE` are not stored.

**Limits (settings).** `WATCH_MAX_QUERIES` (4), `WATCH_MAX_REGIONS` (2), `WATCH_RESULTS_PER_QUERY` (5),
`WATCH_MAX_CANDIDATES` (12), `WATCH_MAX_PAGES` (4), `WATCH_PAGE_MAX_CHARS` (1500), `WATCH_BATCH_SIZE` (6),
`WATCH_SNIPPET_SCORE_CAP` (60), `WATCH_STRUCTURED_RETRIES` (1), `WATCH_ROUTE_TEXT_CHARS` (400).

**Cost, with the defaults.** At most **8 LLM calls** per run: 2 scoring batches and 2 routing batches, one retry each
(`worst_case_llm_calls()`). At 8000 tokens per minute, a typical run takes about 1 to 2 minutes and the worst case about 5
to 6 minutes (estimates: check the `watch node=... duration_ms=...` logs on your data).

**Progress streams.** `POST /runs/{run_id}/stream-ticket` returns a ticket valid for `STREAM_TICKET_SECONDS` (60),
for that run and that user, usable once. Open `GET /runs/{run_id}/events?ticket=...` for server-sent events:
`progress` (step and status on each change), `done`, `timeout`. The login token never appears in a URL. The events carry
no prompt, answer, query or page text. Polling stays available with `GET /runs/{run_id}`.
A ticket lives in the memory of one process: with several API workers, a ticket must be opened on the worker that issued
it. A shared store is the fix, to be decided with the multi-worker deployment.

**Retention.** Finished research and watch runs, their checkpoints, and watch results older than
`RESULT_RETENTION_DAYS` (365) are purged at startup and every `RESULT_PURGE_INTERVAL_SECONDS` (one day). Running runs
and applications are never purged by this job.

**Deleting an account.** `DELETE /auth/account` with `{"password": "..."}` removes the account and every row that belongs
to it (profile, knowledge, runs, results, applications, messages, tokens, checkpoints), in one transaction. Another user's
data is untouched. A wrong password removes nothing.

**Not yet wired.** The watch cannot be run from the API until its background run (`POST /watch/runs`) and the new profile
fields (regions, languages, offer type) are wired; both need schema migrations, see
`docs/adr/0003-watch-graph-streams-retention.md`. The synchronous `POST /watch/run` was removed: it bypassed the run
pattern (no status, no heartbeat, one model call per result). Results already stored are still listed by `GET /watch/results`.

## Sending email

- **Dry-run is the default.** Nothing is sent. Each send runs every check, then stops.
- Real sending needs **both** `MAIL_DRY_RUN=false` **and** either `MAIL_REDIRECT_TO` (every message goes to a
  test address you own) or `MAIL_ALLOW_REAL_RECIPIENTS=true`. Without one of them the application refuses to start.
- Each real send also needs the recipient address typed again in the request (`confirm_recipient`).
- A send that stops halfway is shown as `needs_verification` after a timeout. It is never retried automatically:
  you check your mailbox and resolve it yourself.

## Running with Docker

`docker compose up -d --build` starts PostgreSQL and the API on port 8000. The API image contains:

- **Tectonic 0.17.0**, the LaTeX compiler for tailored CVs, installed at `/usr/local/bin/tectonic`. The download is pinned
  to this version and checked against the SHA-256 that GitHub publishes for the release asset (amd64 and arm64).
  The build fails on any other digest. Compose sets `TECTONIC_PATH` to that path, because a Windows path from
  `backend/.env` cannot exist in the container.
- **First CV compilation needs the network.** Tectonic downloads the TeX packages it needs into its cache on the first
  run (the cache lives in the `app` user's home inside the container). Later compilations reuse the cache, but a
  fresh container downloads them again. Allow outbound HTTPS from the container, or pre-warm a volume.
- **The API refuses to start without a working Tectonic**, with a message that names `TECTONIC_PATH`. Set
  `TECTONIC_REQUIRED=false` only if you never need CV PDFs; the rest of the application then works.

Outside Docker, install Tectonic yourself and point `TECTONIC_PATH` at it (or put it on `PATH`).

## Frontend (React)

The React frontend lives in `frontend/`: Vite, React, TypeScript (strict), React Router and TanStack Query. Slice 1
covers login and registration, the master CV, new applications with live progress, the review screen, approval and
sending, and the applications list. Slice 2 adds the research page (sourced answers, following the run by its progress
stream with polling as the fallback), profile and knowledge base, the watch with "Prepare application" (which opens the
form prefilled and never prepares anything by itself), connections (LinkedIn and GitHub: connected, expired or not
connected), the content queue (edit, then publish after a confirmation), and the account page (deletion with the
password typed again). The first plain-HTML app was removed once the React screens covered it. Node 26 is the tested version.

### Run it in development

Three terminals, from the repository root (Windows PowerShell):

```powershell
# 1. Database
docker compose up -d db

# 2. Backend (from backend/, with backend/.env filled in)
.\.venv\Scripts\python.exe -m uvicorn linkedin_agent.main:app --reload --app-dir src

# 3. Frontend (from frontend/). First time only: copy the example file and install the dependencies.
Copy-Item .env.example .env
npm ci
npm run dev
```

Open the address printed by Vite, `http://localhost:5173` by default.

Frontend variables (`frontend/.env.example`):

| Variable | Default | Meaning |
| --- | --- | --- |
| `VITE_API_BASE_URL` | `/api` | Path the browser calls. It is always same-origin. |
| `VITE_DEV_PORT` | `5173` | Port of the Vite dev server. |
| `VITE_DEV_API_TARGET` | `http://127.0.0.1:8000` | Where the dev proxy forwards `/api/...` (the `/api` prefix is removed). |

Because of the proxy, the browser never calls the API on another port, so CORS does not apply in development.
`FRONTEND_ORIGIN` in `backend/.env` must be the origin the browser uses: `http://localhost:5173` in development (follow
`VITE_DEV_PORT` if you change it), `http://localhost:8080` with Docker.

Scripts (run from `frontend/`):

| Command | What it does |
| --- | --- |
| `npm run dev` | Dev server with hot reload. |
| `npm run build` | Type check, then production build into `dist/`. |
| `npm run preview` | Serves the production build locally. |
| `npm run lint` | ESLint (strict type-aware rules, React hooks). |
| `npm run format:check` | Prettier check (`npm run format` to fix). |
| `npm run typecheck` | TypeScript, no emit. |
| `npm test` | Vitest with Testing Library (review rules, API client, auth redirect, OpenAPI contract). |
| `npm run gen:types` | Regenerates `src/api/schema.gen.ts` from `openapi.json`. Commit the result. |
| `npm run check:types` | Fails when the committed `schema.gen.ts` differs from what `openapi.json` generates. CI runs it. |

The API client is checked against the backend's OpenAPI snapshot, `frontend/openapi.json`. To refresh the snapshot after a
backend change:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts/dump_openapi.py > ../frontend/openapi.json
cd ../frontend
npm test
```

### Run it with Docker

`docker compose up -d --build` starts the database, the API and the web app. The web app is served by nginx on
`http://localhost:8080` (change the host port with `FRONTEND_PORT`). nginx serves the single-page app and forwards `/api/`
to the API, so the browser sees one origin and no CORS is needed.

### Authentication

- The login token comes from `POST /auth/login`. It is kept in memory and mirrored in `sessionStorage` (key
  `linkedin-agent.session.v1`), so a reload keeps the session and closing the tab ends it.
- The token never goes in a URL, never in `localStorage`, and is never logged. The PDF preview is fetched with the
  token in the `Authorization` header and shown through a temporary blob URL.
- A `401` from the API ends the session and sends the user to the login page with a message. A token that has
  already expired is detected in the browser (its payload is read, not verified); the server stays the judge.
- **Trade-off, decided for slice 1:** any script running on the app's origin can read `sessionStorage`, so an XSS bug
  would expose the token. An `httpOnly` cookie avoids that, but needs backend changes (see below). The storage is in
  one module, `frontend/src/auth/tokenStorage.ts`, so switching later does not touch the screens.

### Backend changes needed

Done in the backend, and used by the frontend:

- **`GET /applications/mail-mode`** returns `{"mode": "dry_run" | "redirected" | "real"}`, computed from the
  configuration only: no address, no secret. The send panel shows that mode, and sending stays disabled while it is
  unknown.
- **`GET /applications/master-cv`** returns the caller's own master CV (404 when none is saved).
- **Password policy.** `GET /auth/password-policy` publishes the limits. Registration refuses a password shorter than
  `PASSWORD_MIN_LENGTH` (default 12, 422 with `code: password_too_short`) or longer than bcrypt's 72 bytes (422 with
  `code: password_too_long`).
- **Email normalisation.** Case and surrounding spaces are removed on registration and login. Accounts created before
  this change with mixed-case addresses no longer match: normalise `users.email` in a data migration before deploying.
- **Failed-login delay.** Failures are counted per (account, client address) pair: the first `LOGIN_FREE_FAILURES`
  (default 3) of a pair are free, then the wait before its next attempt doubles (`LOGIN_BASE_DELAY_SECONDS`, default 2),
  capped at `LOGIN_MAX_DELAY_SECONDS` (default 900). A third party's failures from their own address never slow the owner
  down. Per client address across all accounts there is a higher ceiling, `LOGIN_ADDRESS_FREE_FAILURES` (default 20). An
  attempt during a wait is refused with 429 (`code: rate_limited`, `retry_after_seconds`), even with the right password.
  A quiet period of `LOGIN_FORGET_SECONDS` (default 3600) forgets a count. The state is in memory, per process. See
  [ADR 0004](docs/adr/0004-login-delay-and-trusted-proxies.md).
- **Email addresses** are stored lowercased and trimmed. Migration `0010_normalise_user_emails` applies that to existing
  accounts. If two accounts would share an address once normalised, the migration stops and names them: merge or delete
  one, then run it again. Nothing is changed partly.
- **Client address behind a proxy.** `TRUSTED_PROXIES` lists the reverse proxies whose `X-Forwarded-For` is believed
  (comma-separated IPs or CIDR networks, validated at startup). Empty means the connection's own address is used. In
  development, Vite sends the header (`xfwd: true`); set `TRUSTED_PROXIES=127.0.0.1` in `backend/.env` so the limit sees
  the browser. Behind nginx, the proxy already appends the real address: list the nginx container's address. Do not list
  a range that the published API port can be reached from.
- **Account deletion with a wrong password** answers `403` (not `401`), so the frontend does not treat it as an
  expired session.
- **Connections**: `GET /oauth/connections` returns `connected`, `expired` or `not_connected` for LinkedIn and GitHub.
  `DELETE /oauth/{provider}` deletes the caller's stored token. GitHub's grant is also revoked at GitHub; LinkedIn has no
  public revocation endpoint, so the answer says `not_supported` and the user revokes it in LinkedIn's settings. The OAuth
  callbacks return to the React page `/connections`.
- **Removed**: `POST /agent/ask` and its history endpoints, `POST /apply/prepare/{id}`, and the synchronous
  `POST /watch/run`. They bypassed the safeguards of the application pipeline or the run pattern. Research is the only
  agent endpoint; the watch has no way to run until its background run is wired (see "Not yet wired" above).
- **Content**: `PATCH /content/{id}` edits a post's title or text (not after publication). Publishing a post that is
  already published is refused (409).
- **`cv.pdf`** is declared as `application/pdf` in the OpenAPI.
- **Progress streams** are opened by the frontend with a single-use ticket (`POST /runs/{id}/stream-ticket`, then
  `GET /runs/{id}/events?ticket=`), with reconnection (`frontend/src/api/runStream.ts`).

Still not done:

1. **CORS and production origin.** Not needed while the browser uses the nginx proxy or the Vite proxy. It is needed
   only if the frontend is served from another origin; then `FRONTEND_ORIGIN` must list that origin.
2. **Optional: `httpOnly` cookie authentication** (login sets the cookie, CSRF protection on unsafe methods, a logout
   endpoint). This removes the token from the browser's JavaScript. It is a trade-off to decide in an ADR.

## Retired variables

`FRONTEND_URL` and `CORS_ORIGINS` were replaced by `FRONTEND_ORIGIN`, a single origin without a path. Settings ignores
unknown variables, so the application logs a warning at startup when one of the retired names is set in the
environment or in `backend/.env`.

## Responsible use

- Only the official LinkedIn API is used. There is no browser automation, no auto-comment and no auto-connect.
- Nothing is emailed without your explicit approval, and approval is refused if any claim in the CV or the email
  cannot be traced to your master CV.
- You are responsible for complying with the terms of every service you connect.

## Development

```powershell
cd backend
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m pytest --cov -q
```

Tests never use the network and never touch your development database: they run on a dedicated `*_test` database.

### Test suite speed and markers

- `pytest` (default) runs the fast suite, about 30 seconds on Windows with a local Postgres. It uses an in-memory
  workflow checkpointer and one shared test client. The Postgres integration tests are deselected by default.
- `pytest -m integration` runs the tests that need the real Postgres checkpointer: the migrations of the checkpoint
  tables, and a review pause that survives a restart of the connection pool. CI runs both.
- To see the slowest tests: `pytest --durations=15`.
- The test database: any Postgres with pgvector on port 5433. Example: `docker run -d -p 5433:5432 -e POSTGRES_USER=test
  -e POSTGRES_PASSWORD=test -e POSTGRES_DB=linkedin_test pgvector/pgvector:pg16`, then
  `$env:TEST_DATABASE_URL = "postgresql://test:test@localhost:5433/linkedin_test"`.

### Real LaTeX compilation tests

Three tests compile CVs with the real Tectonic binary and read the PDF text back: the sample CV (header, education and contact built from the master CV, the model's lines), a CV with typographic characters and an accented name, and a character the font lacks, which must be refused. They are skipped unless you point the
`TEST_TECTONIC_PATH` environment variable at the binary. The test suite does not read `TECTONIC_PATH` from
`backend/.env`, so the setting must be given explicitly for this run:

```powershell
cd backend
$env:TEST_TECTONIC_PATH = "C:\tools\tectonic\tectonic.exe"   # full path to your binary
.\.venv\Scripts\python.exe -m pytest tests/test_cv_tectonic_integration.py -q -rs
Remove-Item Env:TEST_TECTONIC_PATH
```

Without the variable the tests report `skipped` with the reason. In CI, the job `cv-compile` installs the pinned Tectonic (checksum-verified, as the Docker image does) and sets `REQUIRE_TECTONIC=1`: then a missing binary is an error, never a skip. A unit test also checks that no test builds a CV with a section the schema forbids. The first run downloads LaTeX packages, so it
needs the network; the test itself does not use the network once they are cached.
