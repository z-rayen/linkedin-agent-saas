"""The deterministic checks of the research answer and of the search results. Pure functions: no model, no network."""

import pytest

from linkedin_agent.graphs.research_checks import (
    FETCHED,
    NONE,
    SNIPPET,
    Evidence,
    check_answer,
    fill_empty_cells,
    has_relative_time,
    normalize_number,
    numbers_in,
    relevance,
)

LABEL = "(according to the search result snippet)"
NOT_SPECIFIED = "not specified in the sources"


def _check(text: str, *evidence: Evidence, require_citations: bool = True):  # type: ignore[no-untyped-def]
    return check_answer(
        text,
        list(evidence),
        snippet_label=LABEL,
        not_specified=NOT_SPECIFIED,
        require_citations=require_citations,
    )


FULL = Evidence(text="Acme published 2,000 offers on 2026-11-10 at 2.5 percent.", fetched=True)
SNIPPET_ONLY = Evidence(text="Acme lists 7,479 offers.", fetched=False)


# --- numbers, by value ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("written", "value"),
    [
        ("2,000", 2000.0),
        ("2000", 2000.0),
        ("2.000", 2000.0),  # European thousands separator
        ("7,497", 7497.0),
        ("7 497", 7497.0),
        ("1,234.5", 1234.5),
        ("2.5", 2.5),
        ("2,5", 2.5),
    ],
)
def test_a_number_is_compared_by_its_value(written: str, value: float) -> None:
    assert normalize_number(written) == value


def test_two_numbers_that_differ_by_a_digit_are_not_equal() -> None:
    assert normalize_number("7,497") != normalize_number("7479")
    assert normalize_number("7,497") == normalize_number("7497")


def test_a_citation_marker_is_not_a_number_of_the_text() -> None:
    assert numbers_in("Claim [12] about 7,497 offers [1].") == {"7497"}


def test_an_iso_date_is_one_unit_not_three_numbers() -> None:
    assert numbers_in("on 2026-11-10") == {"date:2026-11-10"}
    assert numbers_in("on 2026-11-10") != numbers_in("on 2026-11-11")


def test_a_number_the_cited_source_does_not_contain_is_dropped() -> None:
    checked = _check("Acme has 7,497 offers [1].", SNIPPET_ONLY)
    assert checked.text == ""
    assert checked.dropped_numbers == 1
    assert checked.claims == []


def test_an_equal_number_written_differently_is_kept() -> None:
    checked = _check("Acme published 2000 offers [1].", FULL)
    assert checked.text == "Acme published 2000 offers [1]."
    assert checked.dropped_numbers == 0


def test_a_number_with_no_citation_is_never_kept() -> None:
    checked = _check("Acme has 2,000 offers.", FULL)
    assert checked.text == ""
    assert checked.dropped_numbers == 1


def test_a_date_is_checked_part_by_part_against_the_source() -> None:
    assert _check("Published on 2026-11-10 [1].", FULL).text == "Published on 2026-11-10 [1]."
    assert _check("Published on 2026-11-11 [1].", FULL).text == ""


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
def test_a_relative_time_is_removed_whatever_its_source(sentence: str) -> None:
    assert has_relative_time(sentence)
    checked = _check(sentence, FULL)
    assert checked.text == ""
    assert checked.dropped_relative_time == 1


def test_an_absolute_date_is_not_a_relative_time() -> None:
    assert not has_relative_time("Published on 2026-11-10 [1].")


# --- citations and labels ------------------------------------------------------------------------


def test_a_statement_resting_on_a_full_page_is_not_labelled() -> None:
    checked = _check("Acme published 2,000 offers [1].", FULL)
    assert checked.claims[0].basis == FETCHED
    assert LABEL not in checked.text


def test_a_statement_resting_only_on_snippets_is_labelled() -> None:
    checked = _check("Acme lists 7,479 offers [1].", SNIPPET_ONLY)
    assert checked.text == f"Acme lists 7,479 offers [1]. {LABEL}"
    assert checked.claims[0].basis == SNIPPET
    assert checked.labelled == 1


def test_a_statement_with_one_full_page_among_snippets_is_not_labelled() -> None:
    checked = _check("Acme lists 2,000 offers [1][2].", FULL, SNIPPET_ONLY)
    assert checked.claims[0].basis == FETCHED


def test_an_uncited_sentence_is_dropped_and_counted() -> None:
    checked = _check("Acme is hiring. Acme published 2,000 offers [1].", FULL)
    assert checked.text == "Acme published 2,000 offers [1]."
    assert checked.dropped_uncited == 1


def test_uncited_sentences_are_kept_when_citations_are_not_required() -> None:
    checked = _check("Acme is hiring.", FULL, require_citations=False)
    assert checked.text == "Acme is hiring."
    assert checked.claims[0].basis == NONE


def test_a_not_specified_answer_is_kept_without_a_citation() -> None:
    checked = _check("The employer is not specified in the sources.", FULL)
    assert checked.text == "The employer is not specified in the sources."


def test_headings_and_blank_lines_are_structure_and_are_kept() -> None:
    checked = _check("## Results\n\nAcme published 2,000 offers [1].", FULL)
    assert checked.text == "## Results\n\nAcme published 2,000 offers [1]."


def test_list_markers_are_kept_on_their_line() -> None:
    checked = _check("- Acme published 2,000 offers [1].", FULL)
    assert checked.text == "- Acme published 2,000 offers [1]."


# --- tables --------------------------------------------------------------------------------------


def test_an_empty_table_cell_is_filled_with_the_not_specified_wording() -> None:
    row, filled = fill_empty_cells("|  | ML intern | 2026-11-10 [1] |", NOT_SPECIFIED)
    assert filled == 1
    assert row == f"| {NOT_SPECIFIED} | ML intern | 2026-11-10 [1] |"


def test_a_table_row_with_an_unsupported_number_is_dropped() -> None:
    table = "| Company | Offers |\n|---|---|\n| Acme | 7,497 [1] |"
    checked = _check(table, SNIPPET_ONLY)
    assert "7,497" not in checked.text
    assert "| Company | Offers |" in checked.text  # the header has no number: it stays
    assert checked.dropped_numbers == 1


def test_a_table_row_with_a_relative_time_is_dropped() -> None:
    table = "| Acme | posted recently [1] |"
    checked = _check(table, FULL)
    assert checked.text == ""
    assert checked.dropped_relative_time == 1


def test_a_table_with_a_missing_company_gets_the_not_specified_cell() -> None:
    table = "| Company | Role | Date |\n|---|---|---|\n|  | ML intern | 2026-11-10 [1] |"
    checked = _check(table, FULL)
    assert f"| {NOT_SPECIFIED} | ML intern | 2026-11-10 [1] |" in checked.text
    assert checked.cells_filled == 1


# --- relevance of search results -----------------------------------------------------------------


def test_an_on_topic_result_scores_by_the_share_of_query_terms_it_matches() -> None:
    score = relevance(
        "machine learning jobs france",
        "Machine learning engineer job openings",
        "Remote roles.",
        "https://careers.example.com/ml",
    )
    assert score == pytest.approx(0.5)  # machine, learning; "jobs" is not a prefix of "job" (too short to stem)


def test_an_off_topic_result_scores_zero() -> None:
    assert relevance("machine learning jobs", "Best pasta recipes", "Easy dinner.", "https://food.example/pasta") == 0


def test_the_domain_counts_as_evidence() -> None:
    assert relevance("retrieval augmented generation", "A guide", "Notes.", "https://retrieval-augmented.example/") > 0


def test_a_query_with_no_topic_word_keeps_every_result() -> None:
    assert relevance("is it", "Anything", "At all.", "https://x.example/") == 1.0


def test_long_terms_match_on_their_first_five_letters() -> None:
    assert relevance("retrieval", "Retrieve documents fast", "", "https://x.example/") == 1.0
