"""The answer follows the language of the request: its labels, its notes, and the way its numbers are read.

Each language runs end to end through run_research with a fake model. The number tests say what a wrong reading
would let through, so that a figure read with the other convention cannot validate a wrong figure.
"""

import asyncio
from typing import Any

import pytest
from cryptography.fernet import Fernet

from linkedin_agent.core.settings import Settings
from linkedin_agent.infra import llm as llm_module
from linkedin_agent.infra.safe_http import FetchError
from linkedin_agent.services import research_service as rs
from linkedin_agent.services.research_service import run_research

PROFILE = {"field": "Machine Learning", "skills": "Python, NLP", "level": "Junior"}
PAGE = "<html><body><p>Acme posted 7479 offers on 2026-11-10.</p></body></html>"
PLAN = '{"intent": "search", "queries": ["machine learning jobs france"]}'
REFLECT = '{"sufficient": true, "queries": []}'


def _settings() -> Settings:
    return Settings(
        database_url="postgresql://unused/unused_test",
        secret_key="a-long-enough-secret-key",
        token_encryption_key=Fernet.generate_key().decode(),
        groq_api_key="test-key",
    )


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The answer the fake model gives. A test sets it before the run."""
    state: dict[str, Any] = {"answer": ""}

    class Model:
        async def complete(self, prompt: str, **options: object) -> str:
            if "planner of a research agent" in prompt:
                return PLAN
            if "You judge whether the evidence" in prompt:
                return REFLECT
            if "You are a research assistant" in prompt:
                return state["answer"]
            raise AssertionError("unexpected prompt")

    settings = _settings()
    monkeypatch.setattr(llm_module, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_module, "get_llm_client", lambda: Model())

    async def search(query: str, num_results: int = 10) -> list[dict[str, str]]:
        return [{"title": "Acme machine learning jobs France", "link": "https://acme.example/1", "summary": "Roles."}]

    async def fetch(url: str, **_: object) -> str:
        if url == "https://acme.example/1":
            return PAGE
        raise FetchError("unreachable")

    monkeypatch.setattr(rs, "search_web", search)
    monkeypatch.setattr(rs, "fetch_text", fetch)
    return state


async def _no_draft(user_id: int, title: str, text: str) -> int:
    raise AssertionError("no draft was asked for")


def _run(request: str) -> dict[str, Any]:
    return asyncio.run(
        run_research(
            user_id=1,
            request=request,
            profile=PROFILE,
            history="",
            draft_post=False,
            save_draft=_no_draft,
            settings=_settings(),
        )
    )


# --- the language of the answer ----------------------------------------------------------------------


def test_a_french_request_gets_a_french_answer_with_french_labels(model: dict[str, Any]) -> None:
    model["answer"] = "Acme publie 7.479 offres [1].\n\n| Entreprise | Poste |\n|---|---|\n|  | 7.479 offres [1] |"
    result = _run("Quelles offres ML en France ?")

    assert result["language"] == "fr"
    assert "Acme publie 7.479 offres [1]." in result["answer"]  # 7.479 is 7479 in French: it matches the page
    assert "| non précisé dans les sources | 7.479 offres [1] |" in result["answer"]
    assert result["answer"].endswith(f"Sources consultées le {result['retrieved_at']}.")


def test_an_english_request_gets_an_english_answer_with_english_labels(model: dict[str, Any]) -> None:
    model["answer"] = "| Company | Role |\n|---|---|\n|  | 2026-11-10 [1] |"
    result = _run("What are the ML jobs in France?")

    assert result["language"] == "en"
    assert "| not specified in the sources | 2026-11-10 [1] |" in result["answer"]
    assert result["answer"].endswith(f"Sources retrieved on {result['retrieved_at']}.")


# --- numbers, read with the convention of the answer's language -----------------------------------


@pytest.mark.parametrize(
    ("request_text", "statement", "kept"),
    [
        ("Quelles offres ML en France ?", "Acme publie 7.479 offres [1].", True),  # French: 7.479 = 7479
        ("Quelles offres ML en France ?", "Acme publie 7,479 offres [1].", False),  # French: 7,479 = 7.479, wrong
        ("What are the ML jobs in France?", "Acme publishes 7,479 offers [1].", True),  # English: 7,479 = 7479
        ("What are the ML jobs in France?", "Acme publishes 7.479 offers [1].", False),  # English: 7.479 = 7.479, wrong
    ],
)
def test_a_figure_is_checked_with_the_convention_of_the_answer_language(
    model: dict[str, Any], request_text: str, statement: str, kept: bool
) -> None:
    model["answer"] = statement
    result = _run(request_text)
    assert (statement in result["answer"]) is kept
    assert sum(result["unsupported"][k] for k in ("numbers_not_found", "numbers_snippet_only")) == (0 if kept else 1)


# --- relative times: counted, and said so --------------------------------------------------------------


def test_removed_relative_time_statements_are_said_so_in_english(model: dict[str, Any]) -> None:
    model["answer"] = "Acme published 7,479 offers on 2026-11-10 [1]. Two more were posted in the last 1-2 weeks [1]."
    result = _run("What are the ML jobs in France?")

    assert result["unsupported"]["relative_time"] == 1
    assert "Note: 1 statement(s) with a relative time" in result["answer"]
    assert "This does not mean that nothing was found." in result["answer"]
    assert "last 1-2 weeks" not in result["answer"]


def test_removed_relative_time_statements_are_said_so_in_french(model: dict[str, Any]) -> None:
    model["answer"] = "Acme publie 7.479 offres le 2026-11-10 [1]. Deux autres ont été publiées récemment [1]."
    result = _run("Quelles offres ML en France ?")

    assert result["unsupported"]["relative_time"] == 1
    assert "Remarque : 1 affirmation(s) à date relative" in result["answer"]
    assert "Cela ne signifie pas qu'aucune offre n'a été trouvée." in result["answer"]


def test_when_every_statement_is_removed_the_answer_does_not_read_as_nothing_found(model: dict[str, Any]) -> None:
    model["answer"] = "There are recent offers [1]. The latest ones are current [1]."
    result = _run("What are the ML jobs in France?")

    assert result["answer"].startswith("None of the statements could be checked against the sources.")
    assert "This does not mean that nothing was found" in result["answer"]
    assert result["unsupported"]["relative_time"] == 2
