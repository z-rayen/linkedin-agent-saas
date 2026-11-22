"""Grounding of the two kinds of name a model can invent: a job title in a research answer, and a link or an e-mail
address in an application email. Each must come from a source the answer cites, or from the master CV."""

from pathlib import Path

from linkedin_agent.core.settings import DEFAULT_RESEARCH_LANGUAGES
from linkedin_agent.cv.grounding import verify_email
from linkedin_agent.cv.models import MasterCV, TailoredCV
from linkedin_agent.cv.pipeline import load_master_cv
from linkedin_agent.graphs.research_checks import (
    FETCHED,
    MISSING_ENTITY,
    OK,
    Evidence,
    assess,
    entities_in,
    support_of,
    title_phrases_in,
)

EN = DEFAULT_RESEARCH_LANGUAGES["en"]
SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "sample_master_cv.json"
MASTER: MasterCV = load_master_cv(SAMPLE)
CV = TailoredCV.model_validate({"lines": [{"section": "skill", "text": "Python", "source_ids": ["skill-py"]}]})


# --- research: job titles ---------------------------------------------------------------------------------------


def test_a_job_title_is_found_in_a_statement_whatever_its_capitals() -> None:
    assert "machine learning engineer" in [
        p.lower() for p in title_phrases_in("Acme hires a machine learning engineer [1].")
    ]


def test_a_bare_role_word_is_not_a_title() -> None:
    assert title_phrases_in("They hire an engineer [1].") == []


def test_a_title_is_an_entity_that_the_cited_source_must_contain() -> None:
    assert "machine learning engineer" in [
        term.lower() for term in entities_in("Acme hires a machine learning engineer [1].")
    ]


def test_a_title_missing_from_the_cited_source_is_refused() -> None:
    statement = "Acme hires a machine learning engineer [1]."
    support = support_of([1], [Evidence(text="Acme publishes offers for data analysts.", fetched=True)], EN)
    assessment = assess(statement, support, EN)
    assert assessment.status == MISSING_ENTITY
    assert "machine learning engineer" in [term.lower() for term in assessment.missing_entities]


def test_a_title_present_in_the_cited_source_is_kept() -> None:
    statement = "Acme hires a machine learning engineer [1]."
    support = support_of([1], [Evidence(text="Acme is hiring a Machine Learning Engineer in Paris.", fetched=True)], EN)
    assessment = assess(statement, support, EN)
    assert assessment.status == OK
    assert assessment.basis == FETCHED


# --- email: links and addresses come from the master CV -------------------------------------------------------------


def _problems(body: str):  # type: ignore[no-untyped-def]
    return [p for p in verify_email("Application", body, CV, MASTER, job_context="") if p.kind == "unknown_source"]


def test_the_contact_address_of_the_master_cv_is_accepted() -> None:
    assert _problems("Please reach me at jane.doe@example.com for a call.") == []


def test_a_link_listed_in_the_master_cv_is_accepted_with_or_without_its_scheme() -> None:
    assert _problems("My profile: https://github.com/example-jane.") == []


def test_an_address_the_master_cv_does_not_contain_is_refused_and_named() -> None:
    problems = _problems("Write to recruiter@other-company.example for details.")
    assert [p.term for p in problems] == ["recruiter@other-company.example"]


def test_a_link_the_master_cv_does_not_contain_is_refused() -> None:
    problems = _problems("See the company page at https://careers.other-company.example/apply.")
    assert len(problems) == 1
    assert problems[0].term.startswith("https://careers.other-company.example")
