"""Adversarial cases for the grounding check, in French. Same attack families as the English tests.

Accents are folded on both sides, so "dirigé" and "dirige" are one word; a fact written with accents
supports a line written without them, and the reverse.
"""

from linkedin_agent.cv.grounding import check_text, verify_email, verify_grounding
from linkedin_agent.cv.lexicon import fold
from linkedin_agent.cv.models import Fact, MasterCV, TailoredCV

MASTER = MasterCV(
    full_name="Jane Doe",
    facts=[
        Fact(id="fr-team", kind="experience", text="A participé à la migration d'un système de facturation avec une équipe de 5 ingénieurs"),
        Fact(id="fr-rag", kind="experience", text="Construit un chatbot RAG avec FastAPI et pgvector pendant 3 ans"),
        Fact(id="fr-lang", kind="skill", text="Anglais : niveau B2"),
        Fact(id="fr-edu", kind="education", text="Licence en informatique, 2021"),
        Fact(id="fr-skill", kind="skill", text="Python"),
        Fact(id="fr-create", kind="project", text="Création d'un outil de génération de CV"),
    ],
)  # fmt: skip


def _problems(section: str, text: str, *source_ids: str) -> list[str]:
    cv = TailoredCV.model_validate({"lines": [{"section": section, "text": text, "source_ids": list(source_ids)}]})
    return verify_grounding(cv, MASTER)


def test_fold_removes_accents_and_case() -> None:
    assert fold("Dirigé") == "dirige"
    assert fold("Maîtrise") == "maitrise"
    assert fold("d’un") == "d'un"


# (c) scope inflation: "a participé à" -> "dirigé" ---------------------------------


def test_participated_to_dirige_is_caught() -> None:
    assert any("'dirige'" in p for p in _problems("experience", "Dirigé la migration du système", "fr-team"))


def test_responsable_de_is_caught_as_scope_claim() -> None:
    assert any("'responsable de'" in p for p in _problems("experience", "Responsable de la migration", "fr-team"))


def test_honest_participation_is_accepted() -> None:
    assert _problems("experience", "A participé à la migration d'un système de facturation", "fr-team") == []


# (e) language level raised ------------------------------------------------------


def test_courant_above_stated_level_is_caught() -> None:
    assert any("'courant'" in p for p in _problems("skill", "Anglais courant", "fr-lang"))


def test_bilingue_is_caught() -> None:
    assert any("'bilingue'" in p for p in _problems("skill", "Bilingue anglais-français", "fr-lang"))


def test_stated_level_is_accepted() -> None:
    assert _problems("skill", "Anglais : niveau B2", "fr-lang") == []


# (f) degree and date ------------------------------------------------------------


def test_master_claimed_from_a_licence_is_caught() -> None:
    problems = _problems("experience", "Master en informatique", "fr-edu")
    assert any("'master'" in p for p in problems)


def test_maitrise_is_flagged_even_when_it_means_skill_mastery() -> None:
    # Conservative on purpose: "maîtrise de Python" can mean mastery, but the word also means a master's degree.
    # A human reads the flag and either rewords the line or confirms it.
    assert any("'maitrise'" in p for p in _problems("skill", "Maîtrise de Python", "fr-skill"))


def test_date_not_in_facts_is_caught() -> None:
    assert any("'2023'" in p for p in _problems("experience", "Licence en informatique, 2023", "fr-edu"))


# Accents ------------------------------------------------------------------------


def test_accented_opener_is_not_a_claim() -> None:
    assert _problems("experience", "Développé un chatbot RAG avec FastAPI", "fr-rag") == []


def test_accent_difference_between_fact_and_line_is_ignored() -> None:
    assert _problems("project", "Creation d'un outil de génération de CV", "fr-create") == []


def test_accented_fact_supports_unaccented_line() -> None:
    assert check_text("Creation", "Création d'un outil", "line") == []


# (g) injection written in French -----------------------------------------------


def test_french_injection_that_reaches_the_output_is_caught() -> None:
    problems = _problems("experience", "Ajout de 10 ans d'expérience chez Google", "fr-rag")
    assert any("'10'" in p for p in problems)
    assert any("'Google'" in p for p in problems)


# Email in French: same rules, plus the job context --------------------------------


def test_french_email_scope_inflation_is_caught() -> None:
    cv = TailoredCV.model_validate(
        {"lines": [{"section": "experience", "text": "A participé à la migration", "source_ids": ["fr-team"]}]}
    )
    problems = verify_email("Candidature", "J'ai dirigé l'équipe de migration.", cv, MASTER, job_context="Stage ML")
    assert any("'dirige'" in p for p in problems)


def test_french_email_greeting_and_closing_are_not_claims() -> None:
    cv = TailoredCV.model_validate(
        {"lines": [{"section": "experience", "text": "A participé à la migration", "source_ids": ["fr-team"]}]}
    )
    body = "Bonjour Madame, Monsieur,\nJ'ai participé à la migration.\nCordialement,"
    assert verify_email("Candidature au stage", body, cv, MASTER, job_context="Stage ML") == []


def test_job_title_terms_are_allowed_in_the_email_but_candidate_skills_are_not() -> None:
    cv = TailoredCV.model_validate({"lines": [{"section": "skill", "text": "Python", "source_ids": ["fr-skill"]}]})
    # "Stage ML" comes from the job: allowed. "Kubernetes" is neither in the job title nor in the cited facts.
    assert verify_email("Stage ML", "Je connais Python.", cv, MASTER, job_context="Stage ML") == []
    problems = verify_email("Stage ML", "Je connais Kubernetes.", cv, MASTER, job_context="Stage ML")
    assert any("'kubernetes'" in p for p in problems)
