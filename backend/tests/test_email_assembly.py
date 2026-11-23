"""The application email is assembled by the code from the candidate's facts. The model writes only the subject, the
opening sentence and the closing sentence.

Permanent cases: the three sentences that a real draft produced and that the check must refuse. They are the reason
for the scope-word list and the mixed-facts check, so they stay here.
"""

import pytest

from linkedin_agent.cv.email import (
    build_body,
    email_facts,
    language_of,
    sentence_of,
    without_fixed_lines,
)
from linkedin_agent.cv.grounding import mixed_sentences, verify_email
from linkedin_agent.cv.latex import render_cv
from linkedin_agent.cv.models import MasterCV, TailoredCV
from linkedin_agent.domain.outputs import ApplicationEmailOutput

FACT_PIPELINE = "Built a RAG pipeline that retrieves document passages from pgvector"
FACT_SERVICE = "Built a question-answering service for internal documents"
FACT_SUMMARY = "Machine learning engineer focused on retrieval systems and evaluation"

MASTER = MasterCV.model_validate(
    {
        "full_name": "Jane Doe",
        "headline": "Machine learning engineer",
        "contact": {"email": "jane.doe@example.com", "location": "Anytown"},
        "facts": [
            {"id": "exp-1", "kind": "experience", "text": FACT_PIPELINE},
            {"id": "exp-2", "kind": "experience", "text": FACT_SERVICE},
            {"id": "sum-1", "kind": "summary", "text": FACT_SUMMARY},
            {"id": "edu-1", "kind": "education", "text": "MSc in Data Science, Anytown University"},
        ],
    }
)
CV = TailoredCV.model_validate(
    {
        "lines": [
            {"section": "experience", "text": FACT_PIPELINE, "source_ids": ["exp-1"]},
            {"section": "experience", "text": FACT_SERVICE, "source_ids": ["exp-2"]},
            {"section": "experience", "text": FACT_SUMMARY, "source_ids": ["sum-1"]},
        ]
    }
)
FACT_TEXTS = [FACT_PIPELINE, FACT_SERVICE]
JOB_CONTEXT = "Machine Learning Intern"


def _kinds(problems: list) -> set[str]:  # type: ignore[type-arg]
    return {problem.kind for problem in problems}


# --- the three sentences that were found in a real draft --------------------------------------------------


def test_designing_and_deploying_is_refused_when_no_fact_says_so() -> None:
    sentence = "I am excited about designing and deploying machine learning systems."
    problems = mixed_sentences(sentence, FACT_TEXTS, "email body") + verify_email(
        "Application", build_body("en", sentence, "", [], MASTER), CV, MASTER, JOB_CONTEXT
    )
    terms = {problem.term for problem in problems if problem.kind == "claim"}
    assert {"designing", "deploying"} <= terms, problems


def test_engineered_rag_pipelines_sentence_is_refused_as_a_mix_of_two_facts() -> None:
    sentence = "I engineered RAG pipelines using pgvector to power a document question-answering service."
    problems = mixed_sentences(sentence, FACT_TEXTS, "email body", JOB_CONTEXT)
    assert _kinds(problems) == {"mixed_facts"}, problems
    assert "pgvector" in problems[0].term and "question-answering" in problems[0].term


def test_engineered_rag_pipelines_sentence_is_refused_as_an_unsupported_scope_word() -> None:
    sentence = "I engineered RAG pipelines using pgvector to power a document question-answering service."
    problems = verify_email("Application", build_body("en", sentence, "", [], MASTER), CV, MASTER, JOB_CONTEXT)
    assert any(p.kind == "claim" and p.term == "engineered" for p in problems), problems


def test_improving_user_experience_is_refused_when_no_fact_says_so() -> None:
    sentence = "I am keen on improving user experience through careful evaluation."
    problems = verify_email("Application", build_body("en", sentence, "", [], MASTER), CV, MASTER, JOB_CONTEXT)
    assert any(p.kind == "claim" and p.term == "improving user experience" for p in problems), problems


def test_a_scope_word_is_allowed_when_a_cited_fact_uses_it() -> None:
    fact = "Designing and deploying retrieval services for internal teams"
    master = MasterCV.model_validate(
        {
            "full_name": "Jane Doe",
            "headline": "x",
            "contact": {"email": "jane.doe@example.com"},
            "facts": [
                {"id": "exp-1", "kind": "experience", "text": fact},
                {"id": "edu-1", "kind": "education", "text": "MSc"},
            ],
        }
    )
    cv = TailoredCV.model_validate({"lines": [{"section": "experience", "text": fact, "source_ids": ["exp-1"]}]})
    sentence = "I am keen on designing and deploying retrieval services."
    assert verify_email("Application", build_body("en", sentence, "", [], master), cv, master, "Intern") == []


# --- the mixing check --------------------------------------------------------------------------------------


def test_a_sentence_that_quotes_one_fact_is_not_a_mix() -> None:
    assert mixed_sentences(FACT_PIPELINE, FACT_TEXTS, "email body") == []


def test_a_sentence_with_one_word_from_each_fact_is_a_mix() -> None:
    problems = mixed_sentences("The pgvector database serves internal documents.", FACT_TEXTS, "email body")
    assert _kinds(problems) == {"mixed_facts"}


def test_shared_generic_words_alone_do_not_make_a_mix() -> None:
    sentence = "I am writing about this role and the team."
    assert mixed_sentences(sentence, FACT_TEXTS, "email body") == []


def test_job_context_words_are_set_aside_in_the_mix_check() -> None:
    sentence = "The machine learning role needs pgvector and internal documents."
    # "machine" and "learning" are in the job title and must not make a mix on their own.
    facts = ["Machine learning with pgvector", "internal documents"]
    assert mixed_sentences(sentence, facts, "email body", "Machine learning intern") != []
    assert mixed_sentences(sentence, ["pgvector work"], "email body", "Machine learning intern") == []


# --- the facts quoted in the email --------------------------------------------------------------------------


def test_email_facts_quote_one_fact_per_sentence_in_cv_order_and_skip_summaries() -> None:
    facts = email_facts(CV, MASTER, limit=5)
    assert facts == [f"{FACT_PIPELINE}.", f"{FACT_SERVICE}."]


def test_email_facts_respect_the_limit() -> None:
    assert email_facts(CV, MASTER, limit=1) == [f"{FACT_PIPELINE}."]


def test_sentence_of_adds_a_full_stop_only_when_missing() -> None:
    assert sentence_of("Built a service") == "Built a service."
    assert sentence_of("Built a service.") == "Built a service."
    assert sentence_of("Built a service?") == "Built a service?"


# --- greeting and closing, by the code, in the language of the job title ----------------------------------


@pytest.mark.parametrize(
    ("title", "language"),
    [
        ("Machine Learning Intern", "en"),
        ("Stage Data Science", "fr"),
        ("Ingénieur machine learning", "fr"),
        ("Data Scientist", "en"),
        ("Stage en apprentissage automatique", "fr"),
        ("Engineer", "en"),
    ],
)
def test_the_language_of_the_email_follows_the_job_title(title: str, language: str) -> None:
    assert language_of(title) == language


def test_a_french_email_has_a_french_greeting_closing_and_signature() -> None:
    body = build_body("fr", "Je souhaite postuler au poste.", "Je reste disponible pour un entretien.", [], MASTER)
    assert body.startswith("Madame, Monsieur,\n\nJe souhaite postuler")
    assert "Je reste disponible pour un entretien.\n\nCordialement,\n\nJane Doe" in body


def test_an_english_email_has_an_english_greeting_closing_and_signature() -> None:
    body = build_body("en", "I am interested in the role.", "I would welcome a conversation.", [], MASTER)
    assert body.startswith("Dear hiring team,\n\nI am interested")
    assert "I would welcome a conversation.\n\nBest regards,\n\nJane Doe" in body


def test_the_facts_sit_between_the_model_sentences_as_their_own_paragraph() -> None:
    body = build_body("en", "Opening.", "Closing.", [f"{FACT_PIPELINE}.", f"{FACT_SERVICE}."], MASTER)
    assert f"Opening.\n\n{FACT_PIPELINE}. {FACT_SERVICE}.\n\nClosing.\n\nBest regards," in body


def test_the_fixed_lines_are_not_checked_as_claims_of_the_model() -> None:
    # "Madame", "Monsieur" and "Cordialement" are capitalised words that no fact contains: they must not be flagged.
    body = build_body("fr", "Je souhaite postuler.", "Je reste disponible.", [f"{FACT_PIPELINE}."], MASTER)
    assert without_fixed_lines(body, MASTER) == f"Je souhaite postuler.\n\n{FACT_PIPELINE}.\n\nJe reste disponible."
    assert verify_email("Candidature", body, CV, MASTER, "Stage Data Science") == []


def test_a_built_email_with_grounded_sentences_has_no_problem() -> None:
    body = build_body(
        "en", "I am interested in the role.", "I would welcome a conversation.", [f"{FACT_PIPELINE}."], MASTER
    )
    assert verify_email("Application for Machine Learning Intern", body, CV, MASTER, JOB_CONTEXT) == []


def test_the_model_output_has_opening_and_closing_and_no_body() -> None:
    output = ApplicationEmailOutput.model_validate({"subject": "Application", "opening": "Hi.", "closing": "Thanks."})
    assert (output.opening, output.closing) == ("Hi.", "Thanks.")
    assert not hasattr(output, "body")


# --- the summary fact is the summary paragraph of the CV, not an experience bullet -------------------------


def test_a_line_based_on_a_summary_fact_is_rendered_as_the_summary_paragraph() -> None:
    source = render_cv(MASTER, CV)
    summary_start = source.index("\\section*{Summary}")
    summary_text = source[summary_start : source.index("\\section*{", summary_start + 1)]
    assert FACT_SUMMARY in summary_text
    assert "\\item " + FACT_SUMMARY not in source
    experience = source[source.index("\\section*{Experience}") : source.index("\\end{document}")]
    assert experience.count("\\item") == 2  # the two experience facts only


def test_a_summary_fact_is_not_rendered_as_an_experience_bullet_even_when_the_model_chose_experience() -> None:
    source = render_cv(MASTER, CV)
    experience = source[source.index("\\section*{Experience}") :]
    assert FACT_SUMMARY not in experience
