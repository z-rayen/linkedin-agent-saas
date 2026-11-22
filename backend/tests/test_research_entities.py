"""Entity grounding of research answers, citation-only fragments, the caution on counts from different pages, and the
basis of every claim after the checks. The model, the search and the page fetch are fakes; no network.
"""

import asyncio
from typing import Any

import pytest
from cryptography.fernet import Fernet

from linkedin_agent.core.settings import DEFAULT_RESEARCH_LANGUAGES, Settings
from linkedin_agent.graphs.research_checks import FETCHED, SNIPPET, Evidence, check_answer
from linkedin_agent.infra import llm as llm_module
from linkedin_agent.infra.safe_http import FetchError
from linkedin_agent.services import research_service as rs
from linkedin_agent.services.research_service import run_research

EN = DEFAULT_RESEARCH_LANGUAGES["en"]
FR = DEFAULT_RESEARCH_LANGUAGES["fr"]
LABELS = tuple(pack.snippet_label for pack in DEFAULT_RESEARCH_LANGUAGES.values())

# The failure reported: a snippet that gives only a count and a title. No employer is in it.
COUNT_TITLE = "Plus de 1 250 Offres ML en France"
COUNT_SNIPPET = "1 250 résultats pour cette recherche."


def _check(text: str, *evidence: Evidence, language: Any = FR) -> Any:
    return check_answer(text, list(evidence), language=language, require_citations=True, known_labels=LABELS)


# --- 1. entity grounding --------------------------------------------------------------------------------


def test_a_company_name_absent_from_a_count_snippet_is_removed_and_counted() -> None:
    table = "| Entreprise | Offres |\n|---|---|\n| Air France | 1 250 offres [1] |"
    snippet = Evidence(text=f"{COUNT_TITLE}. {COUNT_SNIPPET}", fetched=False)
    checked = _check(table, snippet)

    assert "Air France" not in checked.text
    assert f"| {FR.not_specified} | 1 250 offres [1] {FR.snippet_label} |" in checked.text
    assert checked.dropped_entities == 1
    assert checked.claims[0].basis == SNIPPET  # the figure rests on the snippet, and the row says so


def test_every_company_of_a_listing_is_removed_when_no_source_names_it() -> None:
    table = "| Entreprise | Offres |\n|---|---|\n| Airbus | 1 250 offres [1] |\n| Criteo | 1 250 offres [1] |"
    snippet = Evidence(text=f"{COUNT_TITLE}. {COUNT_SNIPPET}", fetched=False)
    checked = _check(table, snippet)

    assert "Airbus" not in checked.text and "Criteo" not in checked.text
    assert checked.dropped_entities == 2


def test_a_name_that_the_cited_source_contains_is_kept_whatever_its_case_and_accents() -> None:
    table = "| Entreprise | Offres |\n|---|---|\n| BNP Paribas | 40 offres [1] |"
    page = Evidence(text="bnp paribas recrute : 40 offres publiées.", fetched=True)
    checked = _check(table, page)

    assert "BNP Paribas" in checked.text
    assert checked.dropped_entities == 0


def test_a_title_word_with_accents_matches_the_source_without_them() -> None:
    sentence = "Ingénieur ML recherché à Paris [1]."
    page = Evidence(text="Offre : ingenieur ml recherche a paris.", fetched=True)
    checked = _check(sentence, page, language=EN)

    assert checked.claims and checked.claims[0].basis == FETCHED


def test_a_name_found_only_in_a_snippet_is_labelled_not_removed() -> None:
    sentence = "Airbus recrute des ingénieurs [1]."
    snippet = Evidence(text="Airbus : 300 offres ingénieurs.", fetched=False)
    checked = _check(sentence, snippet, language=EN)

    assert checked.claims[0].basis == SNIPPET
    assert checked.text.endswith(EN.snippet_label)


def test_the_exact_reported_failure_end_to_end_names_no_employer(monkeypatch: pytest.MonkeyPatch) -> None:
    """A count and a title from a search page; the model adds employers that the snippet never names."""
    settings = Settings(
        database_url="postgresql://unused/unused_test",
        secret_key="a-long-enough-secret-key",
        token_encryption_key=Fernet.generate_key().decode(),
        groq_api_key="test-key",
    )

    class Model:
        async def complete(self, prompt: str, **options: object) -> str:
            if "planner of a research agent" in prompt:
                return '{"intent": "search", "queries": ["offres ML France"]}'
            if "You judge whether the evidence" in prompt:
                return '{"sufficient": true, "queries": []}'
            if "You are a research assistant" in prompt:
                return (
                    "| Entreprise | Offres |\n|---|---|\n"
                    "| Air France | 1 250 offres [1] |\n"
                    "| BNP Paribas | 1 250 offres [1] |\n"
                    "| Capgemini | 1 250 offres [1] |"
                )
            raise AssertionError("unexpected prompt")

    monkeypatch.setattr(llm_module, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_module, "get_llm_client", lambda: Model())

    async def search(query: str, num_results: int = 10) -> list[dict[str, str]]:
        return [{"title": COUNT_TITLE, "link": "https://www.indeed.example/jobs?q=ml", "summary": COUNT_SNIPPET}]

    async def fetch(url: str, **_: object) -> str:
        raise FetchError("the results page is refused")

    monkeypatch.setattr(rs, "search_web", search)
    monkeypatch.setattr(rs, "fetch_text", fetch)

    async def no_draft(user_id: int, title: str, text: str) -> int:
        raise AssertionError("no post was asked for")

    result = asyncio.run(
        run_research(
            user_id=1,
            request="Quelles offres ML en France ?",
            profile={"field": "ML", "skills": "Python", "level": "Junior"},
            history="",
            draft_post=False,
            save_draft=no_draft,
            settings=settings,
        )
    )

    for employer in ("Air France", "BNP Paribas", "Capgemini"):
        assert employer not in result["answer"]
    assert result["unsupported"]["entities_not_found"] == 3
    assert all(claim["basis"] == SNIPPET for claim in result["claims"])


# --- 2. the snippet rule in the prompt ----------------------------------------------------------------------


def test_the_prompt_forbids_adding_examples_to_a_snippet_only_source() -> None:
    from linkedin_agent.prompts import render

    prompt = render(
        "research_answer.v3", field="ML", skills="x", level="y", history="(none)", request="r", sources="s",
        not_specified="non précisé dans les sources",
    )  # fmt: skip
    assert 'A source marked "search snippet only" supports only what its text literally says' in prompt
    assert "Do not invent examples of job listings" in prompt
    assert "Name a company, a job or an example only when the snippet text itself contains it" in prompt


# --- 3. citation-only fragments are dropped -----------------------------------------------------------------


@pytest.mark.parametrize("fragment", ["- [1]", "[2]", "1. [1][2]", "* [3]"])
def test_a_line_of_citations_alone_asserts_nothing_and_is_dropped(fragment: str) -> None:
    text = f"Acme publie 1 250 offres [1].\n{fragment}"
    page = Evidence(text="Acme publie 1 250 offres.", fetched=True)
    checked = _check(text, page, page, page)

    assert fragment not in checked.text.split("\n")
    assert [claim.text for claim in checked.claims] == ["Acme publie 1 250 offres [1]."]


# --- 4. the caution on counts from different pages -----------------------------------------------------------


def test_counts_from_two_search_pages_bring_a_caution_in_the_language_of_the_answer() -> None:
    table = "| Source | Offres |\n|---|---|\n| Indeed | 1 250 offres [1] |\n| Glassdoor | 300 offres [2] |"
    indeed = Evidence(text="Indeed : 1 250 offres ML.", fetched=True)
    glassdoor = Evidence(text="Glassdoor : 300 offres ML.", fetched=True)
    checked = _check(table, indeed, glassdoor)
    assert checked.count_caution is True


def test_a_single_search_page_brings_no_caution() -> None:
    checked = _check("Indeed publie 1 250 offres [1].", Evidence(text="Indeed : 1 250 offres ML.", fetched=True))
    assert checked.count_caution is False


def test_the_caution_text_is_written_in_the_language_of_the_answer() -> None:
    assert "not comparable" in EN.count_caution and "must not be added up" in EN.count_caution
    assert "ne sont pas comparables" in FR.count_caution and "additionnés" in FR.count_caution


# --- 5. the basis of every claim, after the removals ----------------------------------------------------------


def test_every_claim_is_labelled_exactly_when_its_basis_is_snippet() -> None:
    text = (
        "| Entreprise | Offres |\n|---|---|\n"
        "| Air France | 1 250 offres [1] |\n"
        "| Acme | 1 250 offres [2] |\n"
        "Acme publie 1 250 offres [1]. Acme recrute [2]."
    )
    page = Evidence(text=f"{COUNT_TITLE}. Acme publie 1 250 offres.", fetched=True)
    snippet = Evidence(text=f"{COUNT_TITLE}. {COUNT_SNIPPET} Acme recrute.", fetched=False)
    checked = _check(text, page, snippet)

    assert checked.claims
    for claim in checked.claims:
        labelled = FR.snippet_label in claim.text
        assert labelled is (claim.basis == SNIPPET), claim.text


def test_a_row_whose_name_was_removed_takes_the_basis_of_what_remains() -> None:
    # "Air France" is removed. The figure rests on a full page, so the row is fetched, not snippet-based.
    table = "| Entreprise | Offres |\n|---|---|\n| Air France | 1 250 offres [1] |"
    page = Evidence(text=f"{COUNT_TITLE}. 1 250 offres publiées.", fetched=True)
    checked = _check(table, page)

    assert checked.claims[0].basis == FETCHED
    assert FR.snippet_label not in checked.text
