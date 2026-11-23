"""A company written in lowercase in a research answer must be found in the cited sources, the way a capitalised one is.

The capgemini case is the reference: "at capgemini" is a claim about Capgemini, and a source that does not name it does
not support it. Case and accents are ignored on both sides."""

from linkedin_agent.core.settings import DEFAULT_RESEARCH_LANGUAGES
from linkedin_agent.graphs.research_checks import (
    MISSING_ENTITY,
    OK,
    Evidence,
    assess,
    entities_in,
    lowercase_companies_in,
    support_of,
)

EN = DEFAULT_RESEARCH_LANGUAGES["en"]
FR = DEFAULT_RESEARCH_LANGUAGES["fr"]


def _assessment(statement: str, source: str, language=EN):  # type: ignore[no-untyped-def]
    support = support_of([1], [Evidence(text=source, fetched=True)], language)
    return assess(statement, support, language)


def test_a_lowercase_company_after_at_is_an_entity() -> None:
    assert "capgemini" in [term.lower() for term in entities_in("The role is at capgemini [1].")]


def test_capgemini_in_lowercase_is_refused_when_the_cited_source_does_not_name_it() -> None:
    result = _assessment("The role is at capgemini [1].", "A machine learning role in Paris for junior engineers.")
    assert result.status == MISSING_ENTITY
    assert "capgemini" in [term.lower() for term in result.missing_entities]


def test_capgemini_in_lowercase_is_kept_when_the_source_names_it_in_capitals() -> None:
    result = _assessment("The role is at capgemini [1].", "Capgemini is hiring a machine learning engineer in Paris.")
    assert result.status == OK


def test_accents_and_case_are_ignored_on_both_sides() -> None:
    result = _assessment("Le poste est chez société générale [1].", "Societe Generale recrute un ingénieur.", FR)
    assert result.status == OK


def test_common_words_after_at_or_by_are_not_company_names() -> None:
    for statement in ("It costs at least ten hours [1].", "The default is set by default [1]."):
        assert lowercase_companies_in(statement) == [], statement


def test_a_capitalised_company_is_not_counted_twice() -> None:
    assert lowercase_companies_in("The role is at Capgemini [1].") == []
