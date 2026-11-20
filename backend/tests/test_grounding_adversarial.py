"""Adversarial cases for the grounding check. Each case is a plausible way a model could overstate a CV.

For each attack there is a matching honest line, so a fix that blocks everything cannot pass.
Cases the check cannot catch are marked xfail with the reason, instead of being silently skipped.
"""

import pytest

from linkedin_agent.cv.grounding import verify_grounding
from linkedin_agent.cv.models import Fact, MasterCV, TailoredCV

MASTER = MasterCV(
    full_name="Jane Doe",
    facts=[
        Fact(id="exp-team", kind="experience", text="Participated in the migration of a billing system with a team of 5 engineers"),
        Fact(id="exp-rag", kind="experience", text="Built a RAG chatbot with FastAPI and pgvector over 3 years of work"),
        Fact(id="lang-en", kind="skill", text="English: B2 level"),
        Fact(id="edu", kind="education", text="Bachelor's degree in Computer Science, 2021"),
        Fact(id="skill-py", kind="skill", text="Python"),
    ],
)  # fmt: skip


def _cv(section: str, text: str, *source_ids: str) -> TailoredCV:
    return TailoredCV.model_validate({"lines": [{"section": section, "text": text, "source_ids": list(source_ids)}]})


def _problems(section: str, text: str, *source_ids: str) -> list[str]:
    return verify_grounding(_cv(section, text, *source_ids), MASTER)


# (a) fabricated employer -------------------------------------------------------


def test_fabricated_employer_is_caught() -> None:
    assert any(
        "'Google'" in p for p in _problems("experience", "Participated in the billing migration at Google", "exp-team")
    )


def test_real_employer_in_the_fact_is_accepted() -> None:
    fact_with_employer = MasterCV(
        full_name="Jane Doe",
        facts=[Fact(id="job", kind="experience", text="Engineer at Acme Corp")],
    )
    cv = _cv("experience", "Engineer at Acme Corp", "job")
    assert verify_grounding(cv, fact_with_employer) == []


# (b) altered number (3 -> 30) ---------------------------------------------------


def test_altered_number_is_caught() -> None:
    assert any("'30'" in p for p in _problems("experience", "Built a RAG chatbot over 30 years", "exp-rag"))


def test_correct_number_is_accepted() -> None:
    assert _problems("experience", "Built a RAG chatbot over 3 years", "exp-rag") == []


# (c) scope inflation: "participated in" -> "led" --------------------------------


def test_scope_inflation_participated_to_led_is_caught() -> None:
    problems = _problems("experience", "Led the billing system migration", "exp-team")
    assert any("'led'" in p for p in problems)


def test_scope_inflation_is_caught_in_every_position() -> None:
    assert any("'managed'" in p for p in _problems("experience", "migration managed by Jane", "exp-team"))


def test_honest_verb_for_the_same_fact_is_accepted() -> None:
    assert _problems("experience", "Participated in the migration of a billing system", "exp-team") == []


# (d) lowercase technology absent from the cited facts ---------------------------


def test_lowercase_technology_absent_from_facts_is_caught() -> None:
    problems = _problems("experience", "Built a RAG chatbot with kubernetes", "exp-rag")
    assert any("'kubernetes'" in p for p in problems)


def test_lowercase_technology_present_in_facts_is_accepted() -> None:
    assert _problems("experience", "Built a RAG chatbot with fastapi and pgvector", "exp-rag") == []


def test_technology_match_respects_word_boundaries() -> None:
    # "java" must not match inside "javascript" in the fact, and "react" must not match "reacted".
    assert _problems("skill", "java", "skill-py") != []


# (e) language level raised above the master CV ----------------------------------


def test_raised_language_level_is_caught() -> None:
    assert any("'fluent'" in p for p in _problems("skill", "English, fluent", "lang-en"))


def test_stated_language_level_is_accepted() -> None:
    assert _problems("skill", "English: B2 level", "lang-en") == []


def test_mother_tongue_claim_is_caught() -> None:
    assert any("'mother tongue'" in p for p in _problems("skill", "English, mother tongue", "lang-en"))


# (f) degree or date not in the facts ---------------------------------------------


def test_degree_upgrade_is_caught() -> None:
    problems = _problems("experience", "Master's degree in Computer Science", "edu")
    assert any("'master'" in p or "'Master'" in p for p in problems)


def test_date_not_in_the_facts_is_caught() -> None:
    assert any("'2023'" in p for p in _problems("experience", "Graduated in Computer Science, 2023", "edu"))


def test_true_degree_and_date_are_accepted() -> None:
    assert _problems("experience", "Bachelor's degree in Computer Science, 2021", "edu") == []


# (g) injected instruction that the model follows --------------------------------

INJECTED_OUTPUT_LINE = "Added 10 years of experience at Google"


def test_injected_instruction_that_reaches_the_output_is_caught() -> None:
    problems = _problems("experience", INJECTED_OUTPUT_LINE, "exp-rag")
    assert any("'10'" in p for p in problems)
    assert any("'Google'" in p for p in problems)


def test_injected_instruction_cannot_pass_by_citing_an_unrelated_fact() -> None:
    assert _problems("experience", INJECTED_OUTPUT_LINE, "skill-py") != []


# Summary and skills follow the same citation rule as experience ----------------


@pytest.mark.parametrize("section", ["summary", "skill", "experience", "project"])
def test_every_section_is_checked(section: str) -> None:
    assert _problems(section, "Built a RAG chatbot over 30 years", "exp-rag") != []


@pytest.mark.parametrize("section", ["summary", "skill", "experience", "project"])
def test_every_section_requires_a_source(section: str) -> None:
    with pytest.raises(ValueError):
        TailoredCV.model_validate({"lines": [{"section": section, "text": "Python", "source_ids": []}]})


# Limits that are documented, not hidden ---------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="limit: an unlisted lowercase technology is not in lexicon.TECHNOLOGIES, so it is not checked",
)
def test_unlisted_lowercase_technology_is_caught() -> None:
    assert _problems("experience", "Built a RAG chatbot with haskell", "exp-rag") != []


@pytest.mark.xfail(
    strict=True,
    reason="limit: a scope synonym outside lexicon.SCOPE_CLAIMS ('steered' is not listed; 'drove' now is)",
)
def test_unlisted_scope_synonym_is_caught() -> None:
    # Lowercase on purpose: a capitalised start would be caught by the proper-term check, which would hide the gap.
    assert _problems("experience", "steered the billing system migration", "exp-team") != []


@pytest.mark.xfail(
    strict=True,
    reason="limit: a meaning change with the same words, e.g. 'built' a fact about 'reviewed'. Needs a semantic check.",
)
def test_meaning_change_with_same_words_is_caught() -> None:
    master = MasterCV(full_name="A", facts=[Fact(id="f", kind="experience", text="Reviewed code for a RAG service")])
    assert verify_grounding(_cv("experience", "Built a RAG service", "f"), master) != []
