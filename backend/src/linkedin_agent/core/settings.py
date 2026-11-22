"""Typed application settings, loaded from environment variables and an optional .env file.

Environment variable names match the ones already used by the prototype, so an
existing backend/.env keeps working. Secrets have no default: the app refuses to
start without them. Non-secret tuning values have defaults, so they can be
overridden per deployment without touching code.
"""

import os
from collections.abc import Mapping
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ReasoningEffort = Literal["", "low", "medium", "high"]  # "" means: not sent, the provider default applies


class LLMStepOverride(BaseModel):
    """The model and reasoning effort of one LLM step. An empty field means: the global setting."""

    model: str = ""
    reasoning_effort: ReasoningEffort = ""


class ResearchLanguage(BaseModel):
    """The words, the number conventions and the fixed wording of the research answer in one language."""

    stopwords: list[str] = Field(default_factory=list)  # used to detect the language of a request
    decimal_separator: Literal[".", ","] = "."
    thousands_separator: Literal[".", ","] = ","  # a space is always accepted as a thousands separator
    snippet_label: str  # appended to a statement that rests only on search snippets
    retrieval_line: str  # must contain "{date}": the retrieval date, which ends every answer that has sources
    not_specified: str  # written in an empty table cell, and as the answer to a fact the sources do not give
    relative_time_removed: str  # must contain "{count}": the note that says relative-time statements were removed
    nothing_checked: str  # the answer when no statement could be checked: it must not read as "nothing found"
    count_caution: str  # added when counts from different search pages are reported: they must not be added up

    @model_validator(mode="after")
    def separators_differ(self) -> "ResearchLanguage":
        if self.decimal_separator == self.thousands_separator:
            raise ValueError("the decimal and thousands separators must differ")
        return self


DEFAULT_RESEARCH_LANGUAGES: dict[str, ResearchLanguage] = {
    "en": ResearchLanguage(
        stopwords=[
            "the",
            "and",
            "of",
            "in",
            "for",
            "are",
            "is",
            "what",
            "which",
            "with",
            "to",
            "on",
            "at",
            "by",
            "from",
            "how",
            "where",
            "when",
            "about",
            "who",
        ],
        decimal_separator=".",
        thousands_separator=",",
        snippet_label="(according to the search result snippet)",
        retrieval_line="Sources retrieved on {date}.",
        not_specified="not specified in the sources",
        relative_time_removed=(
            'Note: {count} statement(s) with a relative time (such as "recently") were removed, because their date '
            "cannot be checked against the sources. This does not mean that nothing was found."
        ),
        nothing_checked=(
            "None of the statements could be checked against the sources. This does not mean that nothing was found: "
            "the sources retrieved are listed below."
        ),
        count_caution=(
            "Caution: counts taken from different search pages are not comparable, and must not be added up."
        ),
    ),
    "fr": ResearchLanguage(
        stopwords=[
            "le",
            "la",
            "les",
            "des",
            "une",
            "un",
            "et",
            "pour",
            "dans",
            "avec",
            "quels",
            "quelles",
            "quel",
            "quelle",
            "est",
            "sur",
            "que",
            "qui",
            "sont",
            "du",
            "au",
            "aux",
            "en",
            "entre",
            "par",
            "sans",
            "de",
        ],
        decimal_separator=",",
        thousands_separator=".",
        snippet_label="(d'après l'extrait du résultat de recherche)",
        retrieval_line="Sources consultées le {date}.",
        not_specified="non précisé dans les sources",
        relative_time_removed=(
            "Remarque : {count} affirmation(s) à date relative (« récemment », « ces dernières semaines »…) ont été "
            "retirées, car leur date ne peut pas être vérifiée dans les sources. Cela ne signifie pas qu'aucune offre "
            "n'a été trouvée."
        ),
        nothing_checked=(
            "Aucune affirmation n'a pu être vérifiée dans les sources. Cela ne signifie pas qu'aucun résultat n'a été "
            "trouvé : les sources consultées sont listées ci-dessous."
        ),
        count_caution=(
            "Attention : les nombres tirés de pages de résultats différentes ne sont pas comparables, "
            "et ne doivent pas être additionnés."
        ),
    ),
}


class Settings(BaseSettings):
    # Tests set LINKEDIN_AGENT_NO_ENV_FILE=1 before any import, so backend/.env is never read in tests.
    model_config = SettingsConfigDict(
        env_file=None if os.environ.get("LINKEDIN_AGENT_NO_ENV_FILE") == "1" else ".env",
        extra="ignore",
    )

    # Infrastructure
    database_url: str
    # The one variable that names the frontend: CORS allowlist and OAuth redirects both use it. Origin only, no path.
    frontend_origin: str = "http://localhost:5500"
    log_level: str = "INFO"

    # Authentication
    secret_key: str = Field(min_length=16)
    # Fernet key that encrypts OAuth tokens at rest (cryptography.fernet.Fernet.generate_key()).
    token_encryption_key: str
    jwt_algorithm: str = "HS256"
    # bcrypt cost factor. 12 is the production default; tests lower it, because hashing is intentionally slow.
    bcrypt_rounds: int = 12
    access_token_expire_minutes: int = 60 * 24 * 7
    oauth_state_expire_minutes: int = 10

    # LLM (Groq, OpenAI-compatible)
    groq_api_key: str = ""
    groq_url: str = "https://api.groq.com/openai/v1/chat/completions"
    groq_model: str = "openai/gpt-oss-20b"
    groq_timeout_seconds: float = 30.0
    groq_max_retries: int = 2
    groq_retry_wait_seconds: float = 8.0
    # Pause between consecutive LLM calls in one request, to stay under the free-tier tokens/minute quota.
    llm_min_interval_seconds: float = 2.0
    # Shared token budget per process (Groq free tier is about 8000 tokens per minute). Per process, not per cluster.
    llm_tokens_per_minute: int = 8000
    # Output cap sent to the provider, and reserved with the prompt before each call. Reasoning tokens count against
    # it: a reasoning model can spend most of it before the answer starts. The reservation is corrected with real usage.
    llm_max_output_tokens: int = Field(default=2048, gt=0)
    # A call whose answer was cut off (finish_reason=length) is retried with the cap multiplied by this factor...
    llm_output_budget_multiplier: float = Field(default=2.0, gt=1)
    # ...but never above this hard ceiling. Keep ceiling + longest prompt within llm_tokens_per_minute.
    llm_output_token_ceiling: int = Field(default=4096, gt=0)
    # Reasoning effort of the reasoning model: "low", "medium" or "high". Empty: the provider default, not sent.
    llm_reasoning_effort: ReasoningEffort = ""
    # Per-step overrides, JSON in the environment, e.g. {"cv_tailor": {"reasoning_effort": "low"}}.
    # A step without an entry uses groq_model and llm_reasoning_effort.
    llm_step_overrides: dict[str, LLMStepOverride] = Field(default_factory=dict)
    # Grounding revisions per preparation: the graph feeds the exact problems back this many times at most.
    graph_max_revisions: int = 2
    # A running job beats (proof of life) at every graph node, HTTP call and budget wait. A beat is never
    # older than this many seconds while the job waits, so waits longer than this are chunked.
    heartbeat_interval_seconds: float = Field(default=10.0, gt=0)
    # A preparation with no heartbeat for this long is "stalled" (and is recovered at startup). Must be longer than
    # the longest expected silence; the application refuses to start otherwise (services/preparations.py).
    preparation_stale_seconds: float = 300.0
    # Where LangGraph keeps the review checkpoints. "postgres" survives restarts (production). "memory" is for tests
    # and throwaway runs: a restart then leaves active runs without a checkpoint, and they are failed on recovery.
    checkpoint_backend: Literal["postgres", "memory"] = "postgres"
    # Checkpointer connection (LangGraph Postgres). Empty means: DATABASE_URL with the +psycopg2 driver suffix removed.
    checkpoint_database_url: SecretStr = SecretStr("")
    # One connection pool per process, opened at startup and closed at shutdown (not one connection per run).
    checkpoint_pool_max_size: int = 4
    # Local embedding model (384 dimensions, FR/EN). Must match the Vector(384) column.
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    # Where fastembed stores the model files, downloaded once. Relative paths resolve from the working directory,
    # like private_data_dir. Not the OS temp directory, which the OS may clear.
    fastembed_cache_dir: str = "data/models/fastembed"
    # Load the embedding model at API startup, so the first request does not wait for the download.
    embedding_warm_on_startup: bool = True

    # Research agent graph (graphs/research_graph.py). Every limit that bounds its cost or its reading.
    # How many times reflection may send the run back to search. Search rounds in total: this value + 1.
    research_max_loops: int = Field(default=2, ge=0)
    research_max_queries: int = Field(default=3, ge=1)  # queries per planning or reflection step
    research_results_per_query: int = Field(default=5, ge=1)
    research_max_sources: int = Field(default=8, ge=1)  # distinct links kept over the whole run
    research_max_pages: int = Field(default=4, ge=0)  # pages fetched in full over the whole run (the rest: snippets)
    research_page_max_chars: int = Field(default=1500, ge=200)  # characters kept per fetched page
    # Characters of sources that may go into one prompt. About a third of this is the estimated token count.
    research_context_max_chars: int = Field(default=9000, ge=1000)
    # Retries of each structured step (plan, reflection) when its answer does not validate.
    research_structured_retries: int = Field(default=1, ge=0)

    # Watch graph (graphs/watch_graph.py): the background search for offers and certifications.
    # Queries are built from these templates and the profile: {field}, {level}, {offer_type}, {region}, {language}.
    watch_query_templates: list[str] = Field(
        default_factory=lambda: [
            "{offer_type} {field} {region}",
            "{field} {offer_type} {level} {language}",
            "{offer_type} {field} certification {region}",
        ]
    )
    watch_default_offer_type: str = "end-of-studies internship"  # used when the profile has none
    watch_max_queries: int = Field(default=4, ge=1)
    watch_max_regions: int = Field(default=2, ge=1)  # regions of the profile used in the queries
    watch_results_per_query: int = Field(default=5, ge=1)
    watch_max_candidates: int = Field(default=12, ge=1)  # distinct new links that are scored at most
    watch_max_pages: int = Field(default=4, ge=0)  # pages fetched in full over the run; the rest stay snippets
    watch_page_max_chars: int = Field(default=1500, ge=200)
    watch_batch_size: int = Field(default=6, ge=1)  # candidates per structured scoring or routing call
    watch_route_text_chars: int = Field(
        default=400, ge=0
    )  # text per candidate in the routing call (title and link first)
    watch_snippet_score_cap: int = Field(default=60, ge=0, le=100)  # a snippet-only result never scores above this
    watch_structured_retries: int = Field(default=1, ge=0)
    watch_min_relevance: float = Field(default=0.3, ge=0, le=1)
    watch_min_title_matches: int = Field(default=1, ge=0)

    # Retention and streaming (see services/retention.py and services/stream_tickets.py).
    result_retention_days: int = Field(default=365, ge=1)  # stored run results and watch results older than this
    stream_ticket_seconds: int = Field(default=60, ge=5, le=600)  # a progress-stream ticket is valid this long, once
    stream_poll_seconds: float = Field(default=1.0, gt=0)  # how often a progress stream reads the run
    stream_max_seconds: float = Field(default=1800.0, gt=0)  # a stream ends after this, even if the run does not
    result_purge_interval_seconds: float = Field(default=86400.0, gt=0)  # how often the retention job runs
    # A search result is off-topic unless it matches at least this share of the query's topic words, in its title,
    # summary or domain. Off-topic results are not read, not shown and not cited. 0 keeps everything.
    research_min_relevance: float = Field(default=0.3, ge=0, le=1)
    # ... and unless at least this many of the query's topic words appear in its title or domain (capped by the number
    # of topic words in the query). Stops one shared word from keeping an unrelated page. 0 disables the rule.
    research_min_title_matches: int = Field(default=2, ge=0)
    # A sentence of the answer must cite a source. Uncited sentences are dropped (table rows and headings are kept).
    research_require_citations: bool = True
    # The language of the answer is detected from the request's words, among these profiles. Labels, the retrieval line
    # and the number conventions (decimal and thousands separators) come from the profile. Setting this variable
    # replaces the whole table: copy the defaults and add a language.
    research_languages: dict[str, ResearchLanguage] = Field(default_factory=lambda: dict(DEFAULT_RESEARCH_LANGUAGES))
    research_default_language: str = "en"  # used when no language is detected in the request

    # Default profile, used until the user saves their own.
    default_field: str = "Machine Learning, Data Science"
    default_skills: str = "Python, TensorFlow, NLP, MLOps, AWS, SQL"
    default_level: str = "Student / Junior"
    default_min_match_score: int = 70
    default_job_feed_url: str = "https://weworkremotely.com/categories/remote-data-science-jobs.rss"
    default_cert_feed_url: str = "https://aws.amazon.com/blogs/training-and-certification/feed/"

    # Web search (Serper.dev)
    serper_api_key: str = ""
    serper_url: str = "https://google.serper.dev/search"
    serper_timeout_seconds: float = 20.0

    # LinkedIn OAuth app
    linkedin_client_id: str = ""
    linkedin_client_secret: str = ""
    linkedin_redirect_uri: str = ""

    # GitHub OAuth app
    github_client_id: str = ""
    github_client_secret: str = ""
    github_redirect_uri: str = ""

    # Email sending. Dry-run is ON by default: nothing leaves the machine until you turn it off.
    mail_dry_run: bool = True
    mail_daily_cap: int = 10
    mail_min_delay_seconds: float = 300.0
    mail_follow_up_after_days: int = 7
    # No second application to the same company (domain-based key) within this many days of a send.
    mail_company_cooldown_days: int = 30
    # Calendar used for the daily cap. Caps reset at local midnight in this timezone.
    mail_timezone: str = "Africa/Tunis"
    # When set, every real message goes to this address instead of the recipient (end-to-end tests on yourself).
    mail_redirect_to: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from_address: str = ""
    # TLS is mandatory: plaintext SMTP is refused. Port 465 uses implicit TLS, other ports require STARTTLS.
    smtp_timeout_seconds: float = 20.0
    smtp_use_starttls: bool = True
    # A send stuck in "sending" longer than this is shown as "needs verification" and is never retried automatically.
    mail_sending_timeout_seconds: float = 900.0
    # Real recipients need an explicit opt-in. Dry-run off alone is refused (see the validator below).
    mail_allow_real_recipients: bool = False
    # Free-mail providers say nothing about the employer: their domain must not identify a company.
    # Entries ending in ".*" match any suffix (yahoo.*, gmx.* ...). JSON list in the environment.
    free_mail_domains: list[str] = [
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "outlook.fr",
        "hotmail.com",
        "hotmail.fr",
        "live.com",
        "live.fr",
        "msn.com",
        "yahoo.*",
        "ymail.com",
        "aol.com",
        "icloud.com",
        "me.com",
        "proton.me",
        "protonmail.com",
        "gmx.*",
        "orange.fr",
        "free.fr",
        "laposte.net",
        "wanadoo.fr",
        "sfr.fr",
    ]

    # LaTeX compiler for tailored CVs (Tectonic single binary). Empty means: look on PATH.
    tectonic_path: str = ""
    tectonic_timeout_seconds: float = 180.0
    # When true, the API refuses to start without a working Tectonic (fail early, not on the first CV).
    # Tests disable it.
    tectonic_required: bool = True
    # Personal files (real master CV, generated PDFs). Gitignored: never commit this folder.
    private_data_dir: str = "data/private"

    @model_validator(mode="after")
    def output_budget_is_coherent(self) -> "Settings":
        """The ceiling must allow the default cap, and one call's reservation must fit the per-minute budget."""
        if self.llm_output_token_ceiling < self.llm_max_output_tokens:
            raise ValueError("LLM_OUTPUT_TOKEN_CEILING must be at least LLM_MAX_OUTPUT_TOKENS")
        if self.llm_output_token_ceiling > self.llm_tokens_per_minute:
            raise ValueError("LLM_OUTPUT_TOKEN_CEILING must not exceed LLM_TOKENS_PER_MINUTE: one call could never fit")
        return self

    @model_validator(mode="after")
    def real_sending_needs_a_target(self) -> "Settings":
        """Turning dry-run off must never reach real recipients by accident.

        Real sending requires either a redirect (every message goes to a test address) or an explicit opt-in
        to real recipients. Otherwise the application refuses to start.
        """
        if not self.mail_dry_run and not self.mail_redirect_to.strip() and not self.mail_allow_real_recipients:
            raise ValueError(
                "MAIL_DRY_RUN=false requires MAIL_REDIRECT_TO (test address) "
                "or MAIL_ALLOW_REAL_RECIPIENTS=true (explicit opt-in to real recipients)"
            )
        return self

    @field_validator("frontend_origin")
    @classmethod
    def must_be_a_bare_origin(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.path not in ("", "/"):
            raise ValueError("FRONTEND_ORIGIN must be an origin such as http://localhost:5173 (no path)")
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def cors_origins(self) -> list[str]:
        return [self.frontend_origin]

    def checkpoint_url(self) -> str:
        """The connection string for the LangGraph checkpointer (psycopg v3 reads the plain postgresql:// form)."""
        explicit = self.checkpoint_database_url.get_secret_value().strip()
        if explicit:
            return explicit
        return self.database_url.replace("postgresql+psycopg2://", "postgresql://", 1)

    @field_validator("token_encryption_key")
    @classmethod
    def must_be_a_fernet_key(cls, value: str) -> str:
        try:
            Fernet(value.encode())
        except (ValueError, TypeError) as exc:
            raise ValueError("TOKEN_ENCRYPTION_KEY must be a valid Fernet key") from exc
        return value


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings. Cached: environment changes after the first call are ignored."""
    return Settings()


# Variables that were removed. Settings ignores unknown variables, so without this check a leftover FRONTEND_URL
# would be silently ignored and CORS would quietly stop allowing the frontend.
RETIRED_VARIABLES: dict[str, str] = {
    "FRONTEND_URL": "FRONTEND_ORIGIN (an origin such as http://localhost:5173, without a path)",
    "CORS_ORIGINS": "FRONTEND_ORIGIN (one origin: the CORS allowlist is exactly that origin)",
}


def retired_variable_warnings(environ: Mapping[str, str], dotenv: Mapping[str, str | None]) -> list[str]:
    """One message per retired variable found in the environment or in the .env file, naming its replacement."""
    messages: list[str] = []
    for name, replacement in RETIRED_VARIABLES.items():
        sources = [
            source
            for source, values in (("environment", environ), (".env file", dotenv))
            if values.get(name) is not None
        ]
        if sources:
            messages.append(f"{name} is set ({' and '.join(sources)}) but is no longer read: use {replacement} instead")
    return messages
