"""A research run end to end, with a fake model that omits a field and invents a time frame. No network, no database.

The search, the page fetch and the model are replaced. What runs for real: the graph, the relevance filter, the checks
of the answer, the traceability of the output, and the prompts (rendered, then read by the fake).
"""

import asyncio
import re
from typing import Any

import pytest
from cryptography.fernet import Fernet

from linkedin_agent.core.settings import Settings
from linkedin_agent.infra import llm as llm_module
from linkedin_agent.infra.safe_http import FetchError
from linkedin_agent.prompts import render
from linkedin_agent.services import research_service
from linkedin_agent.services.research_service import run_research

PROFILE = {"field": "Machine Learning", "skills": "Python, NLP", "level": "Junior"}
PAGE_HTML = "<html><body><p>Acme posted 7479 offers on 2026-11-10.</p></body></html>"
# The model's answer: one number that is not in the source (7,497 vs 7479), a relative time, an empty table cell
# (the company is not given), an invalid citation, and a table row that is grounded in the full page.
ANSWER = (
    "Acme publishes 7,497 offers [1]. Two more were posted in the last 1-2 weeks [1].\n\n"
    "| Company | Role | Date |\n"
    "|---|---|---|\n"
    "|  | ML intern | 2026-11-10 [1] |\n\n"
    "Other claim [9]."
)


class FakeModel:
    """Answers each prompt kind the way a small model would: it leaves a field out, and invents a time frame."""

    def __init__(self) -> None:
        self.kinds: list[str] = []
        self.answer_prompt = ""

    async def complete(self, prompt: str, **options: object) -> str:
        if "planner of a research agent" in prompt:
            self.kinds.append("plan")
            return '{"intent": "search", "queries": ["machine learning jobs france"]}'
        if "You judge whether the evidence" in prompt:
            self.kinds.append("reflect")
            return '{"sufficient": true, "queries": []}'
        if "You are a research assistant" in prompt:
            self.kinds.append("answer")
            self.answer_prompt = prompt
            return ANSWER
        raise AssertionError("unexpected prompt")


def _settings() -> Settings:
    return Settings(
        database_url="postgresql://unused/unused_test",
        secret_key="a-long-enough-secret-key",
        token_encryption_key=Fernet.generate_key().decode(),
        groq_api_key="test-key",
    )


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> FakeModel:
    model = FakeModel()
    settings = _settings()
    monkeypatch.setattr(llm_module, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_module, "get_llm_client", lambda: model)

    async def search(query: str, num_results: int = 10) -> list[dict[str, str]]:
        return [
            {"title": "Acme machine learning jobs France", "link": "https://acme.example/1", "summary": "Open roles."},
            {"title": "Acme jobs list France", "link": "https://acme.example/jobs-2", "summary": "More roles."},
            {"title": "Best pasta recipes", "link": "https://food.example/pasta", "summary": "Easy dinner."},
        ]

    async def fetch(url: str, **_: object) -> str:
        if url == "https://acme.example/1":
            return PAGE_HTML
        raise FetchError("the page is unreachable")

    monkeypatch.setattr(research_service, "search_web", search)
    monkeypatch.setattr(research_service, "fetch_text", fetch)
    return model


def _run(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "user_id": 1,
        "request": "Quelles offres ML en France ?",
        "profile": PROFILE,
        "history": "",
        "draft_post": False,
        "save_draft": _no_draft,
        "settings": _settings(),
    }
    values.update(overrides)
    return asyncio.run(run_research(**values))


async def _no_draft(user_id: int, title: str, text: str) -> int:
    raise AssertionError("no draft was asked for")


def test_the_answer_keeps_only_what_the_sources_support(world: FakeModel) -> None:
    result = _run()
    answer = result["answer"]

    assert "7,497" not in answer  # the number is not in the page (7479)
    assert "last 1-2 weeks" not in answer  # a relative time has no date to check against
    assert "Other claim" not in answer  # its only citation named no shown source
    assert "| Company | Role | Date |" in answer  # the table structure is kept
    assert "| not specified in the sources | ML intern | 2026-11-10 [1] |" in answer  # the omitted field
    assert result["unsupported"] == {"uncited": 1, "relative_time": 1, "numbers": 1}
    assert result["removed_citations"] == 1


def test_the_answer_says_when_its_sources_were_retrieved(world: FakeModel) -> None:
    result = _run()
    retrieved = result["retrieved_at"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", retrieved)
    assert result["answer"].endswith(f"Sources retrieved on {retrieved}.")


def test_the_output_traces_each_statement_to_its_sources_and_their_basis(world: FakeModel) -> None:
    result = _run()

    assert result["claims"] == [
        {
            "text": "| not specified in the sources | ML intern | 2026-11-10 [1] |",
            "sources": [1],
            "basis": "fetched",
        }
    ]
    assert result["sources"] == [
        {"number": 1, "title": "Acme machine learning jobs France", "link": "https://acme.example/1", "fetched": True}
    ]


def test_only_cited_sources_are_sources_the_others_are_also_found(world: FakeModel) -> None:
    result = _run()
    assert [s["link"] for s in result["sources"]] == ["https://acme.example/1"]
    assert result["also_found"] == [
        {"title": "Acme jobs list France", "link": "https://acme.example/jobs-2", "fetched": False}
    ]


def test_an_off_topic_result_is_not_read_not_shown_and_not_cited(world: FakeModel) -> None:
    result = _run()
    assert result["off_topic"] == 1
    assert all("pasta" not in s["link"] for s in result["sources"] + result["also_found"])


def test_the_answer_prompt_states_the_rules_of_the_checks(world: FakeModel) -> None:
    _run()
    prompt = world.answer_prompt
    assert "full page" in prompt and "search snippet only" in prompt  # every source is marked
    assert 'exactly "not specified in the sources"' in prompt  # the rule for a field the sources do not give
    assert "Never write a relative time" in prompt
    assert "(full page)" in prompt  # the marking reaches the model in the sources block
    assert "Sources retrieved on" not in prompt  # the date is added by code after the model answers


def test_the_not_specified_rule_is_part_of_the_prompt_contract() -> None:
    prompt = render("research_answer.v3", field="ML", skills="x", level="y", history="(none)", request="r", sources="s")
    assert "not specified in the sources" in prompt
    assert "Never guess" in prompt


def test_a_snippet_only_statement_is_labelled_in_the_run(world: FakeModel, monkeypatch: pytest.MonkeyPatch) -> None:
    original = world.complete

    async def snippet_answer(prompt: str, **options: object) -> str:
        if "You are a research assistant" in prompt:
            return "Acme lists offers [2]."  # the second source was never read: a snippet only
        return await original(prompt, **options)

    monkeypatch.setattr(world, "complete", snippet_answer)
    result = _run()
    assert result["claims"][0]["basis"] == "snippet"
    assert "(according to the search result snippet)" in result["answer"]
