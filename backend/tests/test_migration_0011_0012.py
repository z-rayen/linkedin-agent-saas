"""Migrations 0011 and 0012 on a populated database: additive, the existing rows survive, existing watch results are
marked unverified, and the downgrade removes exactly the new columns."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from linkedin_agent.database import engine

BACKEND_DIR = Path(__file__).resolve().parents[1]
BEFORE = "0010_normalise_user_emails"
HEAD = "0012_watch_result_provenance"


def _config() -> Config:
    return Config(str(BACKEND_DIR / "alembic.ini"))


@pytest.fixture
def migrations_back_at_head() -> Iterator[None]:
    yield
    command.upgrade(_config(), "head")


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns(table)}


def test_existing_watch_results_are_kept_and_marked_unverified(
    clean_database: None, migrations_back_at_head: None
) -> None:
    command.downgrade(_config(), BEFORE)
    assert "unverified" not in _columns("watch_results")
    with engine.begin() as conn:
        user_id = conn.execute(
            text(
                "INSERT INTO users (email, hashed_password, created_at) VALUES ('m@example.com', 'x', now()) RETURNING id"
            )
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO profile_config (user_id, field, skills, level, min_match_score) VALUES (:u, 'NLP', 'Python', 'Junior', 70)"
            ),
            {"u": user_id},
        )
        conn.execute(
            text(
                "INSERT INTO watch_results (user_id, type, title, link, match_score, created_at) VALUES (:u, 'job', 'Old offer', 'https://a.example/old', 80, now())"
            ),
            {"u": user_id},
        )

    command.upgrade(_config(), HEAD)

    assert {"regions", "languages", "offer_type"} <= _columns("profile_config")
    assert {"canonical_link", "fetched", "unverified", "company", "job_title"} <= _columns("watch_results")
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT title, match_score, fetched, unverified, canonical_link FROM watch_results")
        ).one()
        profile = conn.execute(text("SELECT field, regions FROM profile_config")).one()
    assert tuple(row) == ("Old offer", 80, False, True, None)  # nothing lost; judged on the snippet, so unverified
    assert tuple(profile) == ("NLP", None)  # the new preference is empty: the settings defaults apply


def test_the_downgrade_removes_exactly_the_new_columns(clean_database: None, migrations_back_at_head: None) -> None:
    command.downgrade(_config(), BEFORE)
    assert "unverified" not in _columns("watch_results")
    assert "regions" not in _columns("profile_config")
    assert "field" in _columns("profile_config") and "type" in _columns("watch_results")
    command.upgrade(_config(), HEAD)
    assert "unverified" in _columns("watch_results")
