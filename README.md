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

- `POST /applications` returns `202` with a run id. `GET /applications/preparations/{run_id}` shows the status
  (`queued`, `running`, `awaiting_review`, `closed`, `failed`), the current step, and whether the run looks stalled.
- The graph pauses for review and never approves or sends. Approval is the existing endpoint, which performs the
  grounding check, the content hash and the frozen PDF itself. Resuming the graph afterwards only closes its record.
- One active run per user, recipient and job title (enforced by the database). A restart resumes stale runs from
  their checkpoint, or marks them failed with an instruction. None stays stuck.

**Worst-case duration.** The graph allows `GRAPH_MAX_REVISIONS=2` revisions. At most 14 LLM calls are made: analysis
(2 with one validation retry), first draft (CV 2, email 2), and two revisions (CV 2 and email 2 each). The research
step uses search only, not the LLM. At the default budget of 8000 tokens per minute, with about 2000 tokens per call,
the worst case takes about 5 to 7 minutes and a typical run about 1 to 2 minutes. These are estimates from the
prompt sizes: check them with the logs (`graph node=... duration_ms=...`) on your own data.

**Token budget.** `LLM_TOKENS_PER_MINUTE` is shared by every call of one process. Each call reserves an estimate,
then corrects it with the usage the provider reports. A call that does not fit waits. A `429` response is a safety
net: its `Retry-After` header is honoured, capped at 60 seconds. **The budget is per process**: run one API worker,
or several workers would together exceed the provider limit.

## Sending email

- **Dry-run is the default.** Nothing is sent. Each send runs every check, then stops.
- Real sending needs **both** `MAIL_DRY_RUN=false` **and** either `MAIL_REDIRECT_TO` (every message goes to a
  test address you own) or `MAIL_ALLOW_REAL_RECIPIENTS=true`. Without one of them the application refuses to start.
- Each real send also needs the recipient address typed again in the request (`confirm_recipient`).
- A send that stops halfway is shown as `needs_verification` after a timeout. It is never retried automatically:
  you check your mailbox and resolve it yourself.

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

### Real LaTeX compilation test (optional)

One test compiles the sample CV with the real Tectonic binary. It is skipped unless you point the
`TEST_TECTONIC_PATH` environment variable at the binary. The test suite does not read `TECTONIC_PATH` from
`backend/.env`, so the setting must be given explicitly for this run:

```powershell
cd backend
$env:TEST_TECTONIC_PATH = "C:\tools\tectonic\tectonic.exe"   # full path to your binary
.\.venv\Scripts\python.exe -m pytest tests/test_cv_tectonic_integration.py -q -rs
Remove-Item Env:TEST_TECTONIC_PATH
```

Without the variable the test reports `skipped` with the reason. The first run downloads LaTeX packages, so it
needs the network; the test itself does not use the network once they are cached.
