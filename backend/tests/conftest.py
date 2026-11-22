"""Shared test fixtures.

Safety: the database URL is forced to a dedicated test database. The developer's
own DATABASE_URL (from backend/.env) is never used by the test suite.

Database and app imports are lazy (inside fixtures), so pure unit tests run
without any database.
"""

import asyncio
import os
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

from sqlalchemy.engine import make_url

if sys.platform == "win32":  # psycopg (async) needs the selector loop on Windows; see main.py
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://test:test@localhost:5433/linkedin_test")

# Hard guard: the suite drops and truncates tables, so it must never point at a database that is not a test database.
# The development database (linkedin_agent) is refused even if someone sets TEST_DATABASE_URL to it by mistake.
_test_db_name = make_url(TEST_DATABASE_URL).database or ""
if not _test_db_name.endswith("_test"):
    raise RuntimeError(
        f"Refusing to run tests against database '{_test_db_name}': the name must end with '_test'. "
        "Set TEST_DATABASE_URL to a dedicated test database."
    )

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
# Tests never read backend/.env. Settings checks this flag at import time, so it must be set before any app import.
os.environ["LINKEDIN_AGENT_NO_ENV_FILE"] = "1"
# Every credential a test could see is replaced by an obvious fake, whatever the developer's shell holds.
for _secret_name in (
    "GROQ_API_KEY",
    "SERPER_API_KEY",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "SMTP_FROM_ADDRESS",
    "LINKEDIN_CLIENT_SECRET",
    "GITHUB_CLIENT_SECRET",
):
    os.environ[_secret_name] = f"test-fake-{_secret_name.lower()}"
# Lowest bcrypt cost passlib accepts. Production keeps 12; tests only need hashing to work, not to be slow.
os.environ["BCRYPT_ROUNDS"] = "4"
# The shared token budget would make tests wait. Budget behaviour is tested with an injected, small budget.
os.environ["LLM_TOKENS_PER_MINUTE"] = "1000000000"
# Send-policy spacing exists for real mail volumes; the tests check the rules explicitly, not the wall clock.
os.environ["MAIL_MIN_DELAY_SECONDS"] = "0"
# The developer's backend/.env must never change test behaviour: these variables override the file.
os.environ["MAIL_DRY_RUN"] = "true"
os.environ["MAIL_REDIRECT_TO"] = ""
os.environ["MAIL_ALLOW_REAL_RECIPIENTS"] = "false"
os.environ["SMTP_HOST"] = ""
# No artificial pauses in tests: they exist to respect the real Groq quota, not to be tested.
os.environ["LLM_MIN_INTERVAL_SECONDS"] = "0"
os.environ["GROQ_RETRY_WAIT_SECONDS"] = "0"
# Unit tests keep the workflow checkpoints in memory. The Postgres checkpointer has its own tests (marker: integration).
os.environ["CHECKPOINT_BACKEND"] = "memory"
# No Tectonic binary is needed to run the suite. The startup check itself is tested directly.
os.environ["TECTONIC_REQUIRED"] = "false"
# The embedding model (hundreds of MB) is never downloaded by the suite: retrieval tests replace the embedder.
os.environ["EMBEDDING_WARM_ON_STARTUP"] = "false"
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["LINKEDIN_CLIENT_ID"] = "test-linkedin-client"
os.environ["LINKEDIN_CLIENT_SECRET"] = "test-fake-linkedin-client-secret"
os.environ["LINKEDIN_REDIRECT_URI"] = "http://localhost:8000/oauth/linkedin/callback"
os.environ["GITHUB_CLIENT_ID"] = "test-github-client"
os.environ["GITHUB_CLIENT_SECRET"] = "test-fake-github-client-secret"
os.environ["GITHUB_REDIRECT_URI"] = "http://localhost:8000/oauth/github/callback"
os.environ["FRONTEND_ORIGIN"] = "http://localhost:5500"

from cryptography.fernet import Fernet  # noqa: E402

# A fresh key per test run. Tokens written during a run are only read back during the same run.
os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

import pytest  # noqa: E402

TABLES = [
    "preparation_runs",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "users",
    "oauth_tokens",
    "profile_config",
    "content_queue",
    "watch_results",
    "agent_messages",
    "profile_knowledge",
    "applications",
]


BACKEND_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def migrated_database() -> None:
    """Bring the test database to the latest Alembic revision, once per test session.

    Tests therefore exercise the same migrations that production runs. Commits do not wait for the disk: the test
    database is disposable, and every test commits several times (fsync per commit made the suite slow).
    """
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import event

    from linkedin_agent.database import engine

    @event.listens_for(engine, "connect")
    def _no_fsync_wait(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("SET synchronous_commit = off")
        cursor.close()

    command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "head")
    engine.dispose()  # connections opened before the listener was attached must not be reused


def _clean_order() -> tuple[list[str], list[str]]:
    """Tables to empty, children first, and the ORM tables that have an integer id (their sequences restart).

    DELETE is used instead of TRUNCATE: on this Postgres a TRUNCATE of 12 tables costs about 0.15 s per test, a
    DELETE per table about 0.02 s. The order comes from the ORM metadata, so a new foreign key cannot break it.
    The checkpoint tables have no foreign keys and are emptied first.
    """
    from linkedin_agent.models import Base

    orm_children_first = [table for table in reversed(Base.metadata.sorted_tables)]
    orm_names = [table.name for table in orm_children_first]
    checkpoint_names = [name for name in TABLES if name not in orm_names]
    with_id = [table.name for table in orm_children_first if "id" in table.c]
    return checkpoint_names + orm_names, with_id


@pytest.fixture
def clean_database(migrated_database: None) -> Iterator[None]:
    """Empty every table before the test, and restart the id sequences as TRUNCATE ... RESTART IDENTITY would.

    Only requested by database-backed tests.
    """
    from sqlalchemy import text

    from linkedin_agent.database import engine
    from linkedin_agent.infra.checkpoint import clear_memory_checkpoints

    delete_order, with_id = _clean_order()
    # One round trip for the whole reset: psycopg runs several statements sent together, in order.
    script = "".join(f"DELETE FROM {table}; " for table in delete_order)
    script += "SELECT " + ", ".join(f"setval(pg_get_serial_sequence('{table}', 'id'), 1, false)" for table in with_id)
    with engine.begin() as conn:
        conn.execute(text(script))
    # The ids restart, so a checkpoint kept from an earlier test would belong to a new run with the same thread id.
    clear_memory_checkpoints()
    yield


@pytest.fixture(scope="session")
def live_client(migrated_database: None) -> Iterator[object]:
    """One test client for the whole session, with the application lifespan started once.

    Opening a client costs about 0.12 s (an event loop per client), and each request on a fresh client costs about
    0.2 s on Windows. State lives in the database, which every test empties first, so one client is enough.
    """
    from fastapi.testclient import TestClient

    from linkedin_agent.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client(clean_database: None, live_client: object, monkeypatch: pytest.MonkeyPatch) -> object:
    """The session client on a clean test database, with embeddings replaced by a constant vector."""
    monkeypatch.setattr("linkedin_agent.services.rag_service.embed", lambda text: [0.1] * 384)
    return live_client


import httpx  # noqa: E402

# Captured before any test patches anything. Every patched factory builds clients from this class.
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _client_factory(handler: Callable[[httpx.Request], httpx.Response]) -> Callable[..., httpx.AsyncClient]:
    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        if kwargs.get("transport") is None:
            kwargs["transport"] = httpx.MockTransport(handler)
        return _REAL_ASYNC_CLIENT(*args, **kwargs)  # type: ignore[arg-type]

    return factory


def _blocked(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError(f"network access is disabled in tests: {request.url}")


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test runs with the network disabled, for httpx and for raw sockets (smtplib, Tectonic's helpers).

    A test that needs HTTP says so with mock_http. A test that needs SMTP injects a fake server.
    """
    import socket

    def refuse(*args: object, **kwargs: object) -> None:
        raise OSError("network access is disabled in tests")

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(_blocked))

    real_getaddrinfo = socket.getaddrinfo

    def only_localhost(host: object, *args: object, **kwargs: object) -> object:
        # The test database is on localhost; psycopg resolves it. Every other name is refused.
        if host in ("localhost", "127.0.0.1", "::1"):
            return real_getaddrinfo(host, *args, **kwargs)  # type: ignore[arg-type]
        raise OSError("DNS resolution is disabled in tests: use a mocked resolver")

    monkeypatch.setattr(socket, "getaddrinfo", only_localhost)
    # Only the high-level entry point that smtplib uses. Patching socket.socket.connect would also break
    # socketpair(), which asyncio relies on under Windows.
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture
def mock_http(monkeypatch: pytest.MonkeyPatch) -> Callable[[Callable[[httpx.Request], httpx.Response]], None]:
    """Answer outgoing httpx.AsyncClient requests with a handler instead of the network.

    Usage: mock_http(lambda request: httpx.Response(200, json={...})). The handler receives httpx.Request objects,
    so tests can assert on URLs, headers and bodies.
    """

    def install(handler: Callable[[httpx.Request], httpx.Response]) -> None:
        monkeypatch.setattr(httpx, "AsyncClient", _client_factory(handler))

    return install


@pytest.fixture
def auth_headers(client) -> dict[str, str]:  # type: ignore[no-untyped-def]
    """Register and log in a test user, return the bearer header."""
    client.post("/auth/register", json={"email": "alice@example.com", "password": "s3cret-pass"})
    token = client.post("/auth/login", json={"email": "alice@example.com", "password": "s3cret-pass"}).json()[
        "access_token"
    ]
    return {"Authorization": f"Bearer {token}"}
