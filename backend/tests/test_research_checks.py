"""The deterministic checks of the research answer and of the search results. Pure functions: no model, no network.

Every number test runs in both languages: a figure read with the wrong convention must not validate a wrong figure.
"""

import pytest

from linkedin_agent.core.settings import DEFAULT_RESEARCH_LANGUAGES
from linkedin_agent.graphs.research_checks import (
    FETCHED,
    NONE,
    SNIPPET,
    Evidence,
    check_answer,
    detect_language,
    fill_empty_cells,
    has_relative_time,
    is_relevant,
    normalize_number,
    numbers_in,
)

EN = DEFAULT_RESEARCH_LANGUAGES["en"]
FR = DEFAULT_RESEARCH_LANGUAGES["fr"]
STOPWORDS = frozenset(word for pack in DEFAULT_RESEARCH_LANGUAGES.values() for word in pack.stopwords)


def _check(text: str, *evidence: Evidence, language=EN, require_citations: bool = True):  # type: ignore[no-untyped-def]
    return check_answer(text, list(evidence), language=language, require_citations=require_citations)


FULL_EN = Evidence(text="Acme published 2,000 offers on 2026-11-10 (2.5 percent of the market).", fetched=True)
SNIPPET_EN = Evidence(text="Acme lists 7,479 offers.", fetched=False)


# --- numbers, by value, in each language ---------------------------------------------------------


@pytest.mark.parametrize(
    ("language", "written", "value"),
    [
        (EN, "2,000", "2000"),  # English: comma groups thousands
        (EN, "2000", "2000"),
        (EN, "2.500", "2.5"),  # English: point is the decimal separator
        (EN, "1,234.5", "1234.5"),
        (EN, "7 497", "7497"),  # a space always groups thousands
        (FR, "2,5", "2.5"),  # French: comma is the decimal separator
        (FR, "2.500", "2500"),  # French: point groups thousands
        (FR, "2.500,5", "2500.5"),
        (FR, "7 497", "7497"),
    ],
)
def test_a_number_is_read_with_the_convention_of_its_language(language, written: str, value: str) -> None:  # type: ignore[no-untyped-def]
    assert normalize_number(written, language) == value


@pytest.mark.parametrize("language", [EN, FR], ids=["en", "fr"])
def test_two_numbers_that_differ_by_a_digit_are_not_equal(language) -> None:  # type: ignore[no-untyped-def]
    assert numbers_in("7,497 offers", language) != numbers_in("7479 offers", language)


def test_the_same_figure_in_english_is_not_the_same_figure_in_french() -> None:
    assert numbers_in("2.500 offers", EN) == {"2.5"}
    assert numbers_in("2.500 offres", FR) == {"2500"}


def test_a_citation_marker_is_not_a_number_of_the_text() -> None:
    assert numbers_in("Claim [12] about 7 497 offers [1].", FR) == {"7497"}


def test_an_iso_date_is_one_unit_not_three_numbers() -> None:
    assert numbers_in("on 2026-11-10", EN) == {"date:2026-11-10"}
    assert numbers_in("on 2026-11-10", EN) != numbers_in("on 2026-11-11", EN)


def test_an_english_figure_written_with_french_convention_is_refused() -> None:
    # The source says 2,500 offers (English). An English answer that writes 2.500 means 2.5: it does not match.
    checked = _check("Acme has 2.500 offers [1].", Evidence(text="Acme has 2,500 offers.", fetched=True))
    assert checked.text == ""
    assert checked.dropped_numbers == 1


def test_a_french_figure_is_checked_with_the_french_convention() -> None:
    kept = _check("Acme a 2.500 offres [1].", Evidence(text="Acme a 2 500 offres.", fetched=True), language=FR)
    assert kept.text == "Acme a 2.500 offres [1]."
    refused = _check("Acme a 2,500 offres [1].", Evidence(text="Acme a 2.500 offres.", fetched=True), language=FR)
    assert refused.text == ""  # 2,5 is not 2500


def test_a_number_the_cited_source_does_not_contain_is_dropped() -> None:
    checked = _check("Acme has 7,497 offers [1].", SNIPPET_EN)
    assert checked.text == ""
    assert checked.dropped_numbers == 1
    assert checked.claims == []


def test_a_number_with_no_citation_is_never_kept() -> None:
    checked = _check("Acme has 2,000 offers.", FULL_EN)
    assert checked.text == ""
    assert checked.dropped_numbers == 1


def test_a_date_is_checked_as_a_whole_against_the_source() -> None:
    assert _check("Published on 2026-11-10 [1].", FULL_EN).text == "Published on 2026-11-10 [1]."
    assert _check("Published on 2026-11-11 [1].", FULL_EN).text == ""


# --- where a number is found: a full page, or only a snippet ------------------------------------


def test_a_number_found_in_a_full_page_is_not_labelled() -> None:
    checked = _check("Acme published 2,000 offers [1].", FULL_EN)
    assert checked.claims[0].basis == FETCHED
    assert "snippet" not in checked.text


def test_a_number_found_only_in_a_snippet_is_labelled_even_when_another_cited_source_is_fully_fetched() -> None:
    # Source 1 is a full page, but the figure 7,479 is only in source 2's snippet: the sentence is snippet-based.
    full_other = Evidence(text="Acme is hiring in Paris.", fetched=True)
    checked = _check("Acme lists 7,479 offers [1][2].", full_other, SNIPPET_EN)
    assert checked.claims[0].basis == SNIPPET
    assert checked.text.endswith(EN.snippet_label)


def test_a_statement_with_no_number_rests_on_a_full_page_when_one_source_is_fetched() -> None:
    checked = _check("Acme is hiring [1][2].", FULL_EN, SNIPPET_EN)
    assert checked.claims[0].basis == FETCHED


def test_a_statement_with_no_number_and_only_snippets_is_labelled() -> None:
    checked = _check("Acme is hiring [1].", SNIPPET_EN)
    assert checked.claims[0].basis == SNIPPET
    assert checked.text == f"Acme is hiring [1]. {EN.snippet_label}"


def test_the_snippet_label_follows_the_language_of_the_answer() -> None:
    snippet_fr = Evidence(text="Acme lance 7 479 offres.", fetched=False)
    checked = _check("Acme lance 7 479 offres [1].", snippet_fr, language=FR)
    assert checked.text == f"Acme lance 7 479 offres [1]. {FR.snippet_label}"


# --- relative time -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "Published in the last 1-2 weeks [1].",
        "The offers were posted recently [1].",
        "These are the current openings [1].",
        "Posted 3 days ago [1].",
        "Ouvert il y a 2 semaines [1].",
        "Publiées ces derniers jours [1].",
    ],
)
def test_a_relative_time_is_removed_and_counted(sentence: str) -> None:
    assert has_relative_time(sentence)
    checked = _check(sentence, FULL_EN)
    assert checked.text == ""
    assert checked.dropped_relative_time == 1


def test_an_absolute_date_is_not_a_relative_time() -> None:
    assert not has_relative_time("Published on 2026-11-10 [1].")


# --- citations, uncited statements, honest "not specified" ---------------------------------------


def test_an_uncited_sentence_is_dropped_and_counted() -> None:
    checked = _check("Acme is hiring. Acme published 2,000 offers [1].", FULL_EN)
    assert checked.text == "Acme published 2,000 offers [1]."
    assert checked.dropped_uncited == 1


def test_uncited_sentences_are_kept_when_citations_are_not_required() -> None:
    checked = _check("Acme is hiring.", FULL_EN, require_citations=False)
    assert checked.text == "Acme is hiring."
    assert checked.claims[0].basis == NONE


def test_a_not_specified_answer_is_kept_without_a_citation() -> None:
    checked = _check("The employer is not specified in the sources.", FULL_EN)
    assert checked.text == "The employer is not specified in the sources."


def test_headings_blank_lines_and_list_markers_keep_their_structure() -> None:
    assert _check("## Results\n\nAcme published 2,000 offers [1].", FULL_EN).text == (
        "## Results\n\nAcme published 2,000 offers [1]."
    )
    assert _check("- Acme published 2,000 offers [1].", FULL_EN).text == "- Acme published 2,000 offers [1]."


# --- tables --------------------------------------------------------------------------------------


def test_an_empty_table_cell_is_filled_with_the_not_specified_wording() -> None:
    row, filled = fill_empty_cells("|  | ML intern | 2026-11-10 [1] |", EN.not_specified)
    assert filled == 1
    assert row == f"| {EN.not_specified} | ML intern | 2026-11-10 [1] |"


def test_the_not_specified_cell_is_written_in_the_answer_language() -> None:
    row, _ = fill_empty_cells("|  | Stage |", FR.not_specified)
    assert row == f"| {FR.not_specified} | Stage |"


def test_a_table_row_with_an_unsupported_number_is_dropped_and_the_header_is_kept() -> None:
    table = "| Company | Offers |\n|---|---|\n| Acme | 7,497 [1] |"
    checked = _check(table, SNIPPET_EN)
    assert "7,497" not in checked.text
    assert "| Company | Offers |" in checked.text
    assert checked.dropped_numbers == 1


def test_a_table_row_with_a_relative_time_is_dropped() -> None:
    checked = _check("| Acme | posted recently [1] |", FULL_EN)
    assert checked.text == ""
    assert checked.dropped_relative_time == 1


def test_a_table_with_a_missing_company_gets_the_not_specified_cell() -> None:
    table = "| Company | Role | Date |\n|---|---|---|\n|  | ML intern | 2026-11-10 [1] |"
    checked = _check(table, FULL_EN)
    assert f"| {EN.not_specified} | ML intern | 2026-11-10 [1] |" in checked.text
    assert checked.cells_filled == 1


def test_a_snippet_only_table_row_gets_the_snippet_label() -> None:
    checked = _check("| Acme | 7,479 offers [1] |", SNIPPET_EN)
    assert checked.text.endswith(EN.snippet_label)


# --- language detection ----------------------------------------------------------------------------


def test_the_language_of_the_request_is_detected_from_its_words() -> None:
    assert detect_language("Quelles offres ML en France ?", DEFAULT_RESEARCH_LANGUAGES, "en") == "fr"
    assert detect_language("What are the ML jobs in France?", DEFAULT_RESEARCH_LANGUAGES, "fr") == "en"


def test_a_request_with_no_stopword_uses_the_default_language() -> None:
    assert detect_language("ML NLP RAG", DEFAULT_RESEARCH_LANGUAGES, "fr") == "fr"
    assert detect_language("ML NLP RAG", DEFAULT_RESEARCH_LANGUAGES, "en") == "en"


# --- relevance of search results ----------------------------------------------------------------


def _relevant(query: str, title: str, summary: str = "", link: str = "https://x.example/", min_title: int = 2) -> bool:
    return is_relevant(
        query,
        title,
        summary,
        link,
        stopwords=STOPWORDS,
        min_share=0.3,
        min_title_matches=min_title,
    )


def test_a_result_matching_enough_terms_in_its_title_is_relevant() -> None:
    assert _relevant("machine learning jobs france", "Machine learning engineer jobs in France")


def test_an_off_topic_result_is_not_relevant() -> None:
    assert not _relevant("machine learning jobs france", "Best pasta recipes", "Easy dinner.", "https://food.example/")


def test_one_shared_word_in_the_summary_is_not_enough() -> None:
    # "jobs" appears in the summary only: no title or domain match, so the result is dropped.
    assert not _relevant("machine learning jobs", "A cooking blog", "Jobs for weekend chefs.", "https://food.example/")


def test_the_domain_counts_as_title_evidence() -> None:
    assert _relevant("retrieval augmented generation", "Notes", "", "https://retrieval-augmented.example/", min_title=2)


def test_an_acronym_can_match_an_unrelated_page_when_it_is_the_only_shared_term() -> None:
    # Documented limit: "RAG" matches a page about dolls. The title-match rule needs two shared topic words to stop
    # most of these, but a query that is only an acronym still keeps them.
    assert _relevant("RAG", "RAG doll collection", min_title=2)


def test_a_two_letter_query_has_no_topic_words_so_every_result_is_kept() -> None:
    assert _relevant("ML", "Best pasta recipes")


def test_long_terms_match_on_their_first_five_letters() -> None:
    assert _relevant("retrieval", "Retrieve documents fast", min_title=1)


def test_the_title_rule_is_capped_by_the_number_of_query_terms() -> None:
    # A query with one topic word cannot be required to match two words in the title.
    assert _relevant("nlp", "NLP course", min_title=2)
