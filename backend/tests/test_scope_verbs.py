"""Scope and leadership verbs, in French and English. Each inflated line claims more than its fact says.

The honest line for each fact is tested too, so that a lexicon which blocks everything cannot pass.
"""

import pytest

from linkedin_agent.cv.grounding import check_text

FR_FACT = "A participé à la migration d'un système de facturation"
EN_FACT = "Participated in the migration of a billing system"


@pytest.mark.parametrize(
    ("line", "claim"),
    [
        ("Mené la migration d'un système de facturation", "mene"),
        ("Supervisé la migration d'un système de facturation", "supervise"),
        ("Piloté la migration d'un système de facturation", "pilote"),
        ("Coordonné la migration d'un système de facturation", "coordonne"),
        ("Encadré la migration d'un système de facturation", "encadre"),
        ("Géré la migration d'un système de facturation", "gere"),
        ("Dirigé la migration d'un système de facturation", "dirige"),
        ("Pris en charge la migration d'un système de facturation", "pris en charge"),
        ("Responsable de la migration d'un système de facturation", "responsable de"),
    ],
)
def test_inflated_french_scope_verbs_are_caught(line: str, claim: str) -> None:
    problems = check_text(line, FR_FACT, "line 1")
    assert any(f"'{claim}'" in problem for problem in problems), problems


@pytest.mark.parametrize(
    ("line", "claim"),
    [
        ("Drove the migration of a billing system", "drove"),
        ("Managed the migration of a billing system", "managed"),
        ("Spearheaded the migration of a billing system", "spearheaded"),
        ("Owned the migration of a billing system", "owned"),
        ("Headed the migration of a billing system", "headed"),
        ("Oversaw the migration of a billing system", "oversaw"),
        ("Coordinated the migration of a billing system", "coordinated"),
        ("Supervised the migration of a billing system", "supervised"),
        ("In charge of the migration of a billing system", "in charge of"),
        ("Took charge of the migration of a billing system", "took charge of"),
        ("Led the migration of a billing system", "led"),
    ],
)
def test_inflated_english_scope_verbs_are_caught(line: str, claim: str) -> None:
    problems = check_text(line, EN_FACT, "line 1")
    assert any(f"'{claim}'" in problem for problem in problems), problems


@pytest.mark.parametrize(
    ("line", "cited"),
    [
        ("Participé à la migration d'un système de facturation", FR_FACT),
        ("Contribué à la migration d'un système de facturation", FR_FACT),
        ("Aidé à la migration d'un système de facturation", FR_FACT),
        ("Participated in the migration of a billing system", EN_FACT),
        ("Helped with the migration of a billing system", EN_FACT),
        ("Dirigé la migration d'un système de facturation", "Dirigé la migration d'un système de facturation"),
        ("Managed the migration", "Managed the migration of a billing system"),
    ],
)
def test_honest_verbs_are_accepted(line: str, cited: str) -> None:
    assert check_text(line, cited, "line 1") == []


def test_scope_claim_taken_from_the_job_description_is_not_accepted() -> None:
    # The job context may supply proper terms and numbers, never scope claims about the candidate.
    problems = check_text("Led the team", EN_FACT, "email body", context_text="Led the team of the internship")
    assert any("'led'" in problem for problem in problems)
