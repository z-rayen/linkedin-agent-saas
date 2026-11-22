"""Output quality of a research answer: table rows, snippet labels written by the model, figures with non-breaking
spaces and in titles, the preference for full pages, and the counts of dropped figures. No model, no network.
"""

import asyncio
from typing import Any

import pytest
from cryptography.fernet import Fernet

from linkedin_agent.core.settings import DEFAULT_RESEARCH_LANGUAGES, Settings
from linkedin_agent.graphs.research_checks import (
    FETCHED,
    SNIPPET,
    Evidence,
    check_answer,
    normalize_number,
    numbers_in,
    split_cells,
)
from linkedin_agent.infra import llm as llm_module
from linkedin_agent.infra.safe_http import FetchError
from linkedin_agent.services import research_service as rs
from linkedin_agent.services.research_service import run_research

EN = DEFAULT_RESEARCH_LANGUAGES["en"]
FR = DEFAULT_RESEARCH_LANGUAGES["fr"]
ALL_LABELS = tuple(pack.snippet_label for pack in DEFAULT_RESEARCH_LANGUAGES.values())
NBSP, NNBSP, THIN = " ", " ", " "


def _check(text: str, *evidence: Evidence, language: Any = FR) -> Any:
    return check_answer(
        text,
        list(evidence),
        language=language,
        require_citations=True,
        known_labels=ALL_LABELS,
    )


def _widths(text: str) -> list[int]:
    """The number of cells of every table row of a text."""
    return [len(split_cells(line)) for line in text.split("\n") if line.strip().startswith("|")]


# --- 1. table rows keep their cells --------------------------------------------------------------


def test_every_row_of_a_table_keeps_the_same_number_of_cells() -> None:
    table = (
        "| Entreprise | Poste | Offres |\n"
        "|---|---|---|\n"
        "| Acme | ML engineer | 1 250 [1] |\n"
        "| Beta | Data scientist | 300 [2] |\n"
        "|  | Stagiaire ML | 1 250 [1] |\n"
    )
    snippet = Evidence(text="Beta recrute des data scientists. 300 offres.", fetched=False)
    full = Evidence(text="Acme ML engineer. 1 250 offres publiées.", fetched=True)
    checked = _check(table, full, snippet)
    assert _widths(checked.text) == [3, 3, 3, 3, 3]  # header, separator, three rows


def test_the_snippet_label_goes_inside_the_last_cell_not_after_it() -> None:
    table = "| Entreprise | Offres |\n|---|---|\n| Beta | 300 [1] |"
    checked = _check(table, Evidence(text="Beta : 300 offres.", fetched=False))
    row = checked.text.split("\n")[-1]
    assert row == f"| Beta | 300 [1] {FR.snippet_label} |"
    assert row.endswith("|")


def test_a_citation_written_after_the_last_column_moves_into_the_last_cell() -> None:
    table = "| Entreprise | Offres |\n|---|---|\n| Acme | 1 250 | [1] |"
    checked = _check(table, Evidence(text="Acme : 1 250 offres.", fetched=True))
    row = checked.text.split("\n")[-1]
    assert _widths(checked.text) == [2, 2, 2]
    assert split_cells(row) == ["Acme", "1 250 [1]"]


def test_a_short_row_is_padded_to_the_width_of_its_table() -> None:
    table = "| A | B | C |\n|---|---|---|\n| x |"
    checked = _check(table, Evidence(text="x", fetched=True))
    assert _widths(checked.text) == [3, 3, 3]


# --- 2. the snippet label is added by code, from the basis only -----------------------------------


def test_a_label_written_by_the_model_on_a_fetched_statement_is_removed() -> None:
    answer = f"Acme publie 1 250 offres [1] {FR.snippet_label}."
    checked = _check(answer, Evidence(text="Acme publie 1 250 offres.", fetched=True))
    assert checked.claims[0].basis == FETCHED
    assert FR.snippet_label not in checked.text
    assert checked.labels_removed == 1


def test_a_label_written_by_the_model_in_a_fetched_table_row_is_removed() -> None:
    table = f"| Acme | 1 250 [1] {FR.snippet_label} |"
    checked = _check(table, Evidence(text="Acme : 1 250 offres.", fetched=True))
    assert FR.snippet_label not in checked.text
    assert checked.claims[0].basis == FETCHED


def test_a_label_of_another_language_written_by_the_model_is_removed_too() -> None:
    checked = _check(
        f"Acme lists 7,479 offers [1]. {EN.snippet_label}",
        Evidence(text="Acme lists 7,479 offers.", fetched=False),
        language=EN,
    )
    assert checked.text.count(EN.snippet_label) == 1  # the one added by code, for the snippet basis
    assert checked.labels_removed == 1


def test_a_snippet_statement_has_exactly_one_label_added_by_code() -> None:
    checked = _check(f"Acme recrute [1] {FR.snippet_label}.", Evidence(text="Acme recrute.", fetched=False))
    assert checked.claims[0].basis == SNIPPET
    assert checked.text.count(FR.snippet_label) == 1


# --- 3. figures with non-breaking and narrow spaces, in titles and bodies -------------------------


@pytest.mark.parametrize("space", [" ", NBSP, NNBSP, THIN], ids=["regular", "nbsp", "narrow-nbsp", "thin"])
def test_a_grouped_figure_matches_whatever_space_groups_it(space: str) -> None:
    assert normalize_number(f"1{space}250", FR) == "1250"
    assert numbers_in(f"Plus de 1{space}250 offres", FR) == {"1250"}


def test_a_figure_in_a_title_counts_for_the_source() -> None:
    title_source = Evidence(text=f"Plus de 1{NBSP}250 Offres ML en France Acme publie.", fetched=True)
    checked = _check("Plus de 1 250 offres [1].", title_source)
    assert checked.claims[0].basis == FETCHED
    assert checked.dropped_numbers == 0


def test_a_figure_with_narrow_spaces_in_the_body_matches_the_answer() -> None:
    body = Evidence(text=f"Acme : 1{NNBSP}250 offres publiées{THIN}ce mois.", fetched=True)
    assert _check("Acme a 1 250 offres [1].", body).claims  # regular space in the answer, narrow in the page


def test_a_figure_found_only_in_a_snippet_is_labelled_not_dropped() -> None:
    checked = _check("Acme a 1 250 offres [1].", Evidence(text="Acme : 1 250 offres.", fetched=False))
    assert checked.claims[0].basis == SNIPPET
    assert checked.dropped_numbers == 0


def test_a_figure_missing_from_a_cited_full_page_is_counted_as_not_found() -> None:
    checked = _check("Acme a 1 250 offres [1].", Evidence(text="Acme : 300 offres.", fetched=True))
    assert checked.text == ""
    assert (checked.dropped_numbers_not_found, checked.dropped_numbers_snippet_only) == (1, 0)


def test_a_figure_missing_from_cited_snippets_is_counted_as_snippet_only() -> None:
    checked = _check("Acme a 1 250 offres [1].", Evidence(text="Acme : 300 offres.", fetched=False))
    assert checked.text == ""
    assert (checked.dropped_numbers_not_found, checked.dropped_numbers_snippet_only) == (0, 1)


# --- 4. the answer prefers the full page --------------------------------------------------------------

PAGE_TITLE = "Plus de 1 250 Offres ML en France"
PAGE_BODY = "Data Scientist, ML Engineer, NLP Engineer. Postes ouverts à Paris et Lyon."
LONG_SNIPPET = "Résumé de résultat de recherche sans détail. " * 20


class _Model:
    """Answers with the offer count when the prompt contains it (the page is in the prompt), else says it is missing."""

    def __init__(self) -> None:
        self.answer_prompts: list[str] = []

    async def complete(self, prompt: str, **options: object) -> str:
        if "planner of a research agent" in prompt:
            return '{"intent": "search", "queries": ["offres ML France"]}'
        if "You judge whether the evidence" in prompt:
            return '{"sufficient": true, "queries": []}'
        if "You are a research assistant" in prompt:
            self.answer_prompts.append(prompt)
            if "Plus de 1 250" in prompt:
                return "Plus de 1 250 offres ML sont publiées en France [1]."
            return "Le nombre d'offres n'est pas précisé dans les sources [1]."
        raise AssertionError("unexpected prompt")


def _settings() -> Settings:
    return Settings(
        database_url="postgresql://unused/unused_test",
        secret_key="a-long-enough-secret-key",
        token_encryption_key=Fernet.generate_key().decode(),
        groq_api_key="test-key",
        research_context_max_chars=1000,  # room for one long snippet, or for the page: not both
    )


def test_a_fetched_page_with_the_figure_is_used_instead_of_not_specified(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _Model()
    settings = _settings()
    monkeypatch.setattr(llm_module, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_module, "get_llm_client", lambda: model)

    async def search(query: str, num_results: int = 10) -> list[dict[str, str]]:
        # The snippet ranks first: a naive order would show it alone, and the page would be left out.
        return [
            {"title": "Offres ML en France", "link": "https://snippet.example/1", "summary": LONG_SNIPPET},
            {"title": PAGE_TITLE, "link": "https://page.example/2", "summary": "Résultat."},
        ]

    async def fetch(url: str, **_: object) -> str:
        if url == "https://page.example/2":
            return f"<html><body><h1>{PAGE_TITLE}</h1><p>{PAGE_BODY}</p></body></html>"
        raise FetchError("unreachable")

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

    assert result["sources"][0]["link"] == "https://page.example/2"  # the page is the source the answer cites
    assert result["sources"][0]["fetched"] is True
    assert "Plus de 1 250 offres" in result["answer"]
    assert "n'est pas précisé" not in result["answer"]
    assert result["claims"][0]["basis"] == FETCHED
    assert result["unsupported"] == {
        "uncited": 0,
        "relative_time": 0,
        "numbers_not_found": 0,
        "numbers_snippet_only": 0,
    }
