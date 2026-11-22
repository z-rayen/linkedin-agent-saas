"""Application quality: placeholders and the signature, numbers by value, neutral and level words, typographic characters,
mandatory parts of the CV, structured grounding problems, and the revision limit in the preparation status.

The grounding check is not loosened: every test here either proves a problem is reported or proves a true statement is
not. The model is a fake; the database is the test database.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from linkedin_agent.core.exceptions import CVCompilationError
from linkedin_agent.cv import compile as compile_module
from linkedin_agent.cv.compile import compile_latex
from linkedin_agent.cv.email import signature, with_signature, without_signature
from linkedin_agent.cv.grounding import GroundingProblem, check_output, check_text, verify_mandatory
from linkedin_agent.cv.latex import escape_latex, render_cv
from linkedin_agent.cv.models import MasterCV, TailoredCV
from linkedin_agent.services.applications import _refusal_message
from tests.test_applications_integrity import (  # noqa: F401  (fixtures are used by name below)
    GROUNDED_CV,
    JOB,
    SAMPLE_MASTER,
    _approve,
    _prepare,
    _setup,
    answers,
    fake_pdf,
)

MASTER = MasterCV.model_validate(SAMPLE_MASTER)
CV = TailoredCV.model_validate(GROUNDED_CV)
SIGNATURE = "Jane Doe\njane.doe@example.com | +00 00 000 000 | Anytown, Country | github.com/example-jane"


def _kinds(problems: list[GroundingProblem]) -> set[str]:
    return {problem.kind for problem in problems}


# --- 1. placeholders, and the signature built by the code -----------------------------------------------


@pytest.mark.parametrize("placeholder", ["[Your Name]", "{company}", "<date>", "[[role]]", "{{title}}"])
def test_an_unfilled_placeholder_is_its_own_problem_kind(placeholder: str) -> None:
    problems = check_text(f"Dear team, I am writing to apply {placeholder}.", "anything", "email body")
    placeholder_problems = [p for p in problems if p.kind == "placeholder"]
    assert placeholder_problems, problems
    assert placeholder_problems[0].term.strip("[]{}<>") != ""
    assert "unfilled placeholder" in placeholder_problems[0]
    assert "must be replaced or removed" in placeholder_problems[0]


def test_the_signature_is_built_from_the_master_cv_and_added_once() -> None:
    body = with_signature("Hello, I am interested in the role.", MASTER)
    assert body.endswith(signature(MASTER))
    assert with_signature(body, MASTER) == body  # adding it again changes nothing
    assert body.count("Jane Doe") == 1


def test_the_signature_is_not_checked_as_a_claim_of_the_model() -> None:
    body = with_signature("Hello, I am interested in the role.", MASTER)
    assert without_signature(body, MASTER) == "Hello, I am interested in the role."
    assert check_output(CV, "Application", body, MASTER, "ML Intern") == []


def test_the_model_body_with_a_placeholder_is_a_problem_even_with_a_signature() -> None:
    body = with_signature("Hello [Your Name], I am interested.", MASTER)
    kinds = _kinds(check_output(CV, "Application", body, MASTER, "ML Intern"))
    assert "placeholder" in kinds


def test_the_email_prompt_forbids_closings_signatures_and_placeholders() -> None:
    from linkedin_agent.prompts import render

    prompt = render(
        "application_email.v2", field="ML", level="Junior", cv_summary="x", job_title="y", feedback="(none)"
    )
    assert "Do NOT write a greeting closing" in prompt
    assert "Never write a placeholder" in prompt


# --- 2. numbers by value --------------------------------------------------------------------------------


@pytest.mark.parametrize("written", ["2,000", "2000", "2 000", "2 000", "2 000", "2.000"])
def test_the_same_number_written_differently_matches(written: str) -> None:
    # The source says 2000 offers. Thousands grouping never changes the value.
    assert check_text(f"we have {written} offers.", "2000 offers", "line 1") == []


@pytest.mark.parametrize(("claimed", "cited"), [("3", "30"), ("30", "3"), ("35", "53"), ("53", "35")])
def test_different_numbers_do_not_match_even_with_the_same_digits(claimed: str, cited: str) -> None:
    problems = check_text(f"Led {claimed} people.", f"Team of {cited} people.", "line 1")
    assert any(p.kind == "number" and p.term == claimed for p in problems), problems


# --- 3. neutral words are neutral; level and scope words are still claims -------------------------------


@pytest.mark.parametrize(
    "letter",
    ["Your team, Dear hiring team, Best regards, Thank you.", "Cordialement, Merci, Bien à vous, Madame, Monsieur."],
)
def test_politeness_words_assert_nothing(letter: str) -> None:
    assert check_text(letter, "no facts here", "email body") == []


def test_i_am_proficient_in_docker_and_sql_is_flagged_as_a_claim() -> None:
    # A permanent test: a level word and two technologies that the cited facts do not contain.
    problems = check_text("I am proficient in Docker and SQL", "Python and FastAPI", "line 1")
    messages = [str(p) for p in problems]
    assert any("claim 'proficient'" in m for m in messages), messages
    assert any("technology 'docker'" in m for m in messages), messages
    assert any("technology 'sql'" in m for m in messages), messages


@pytest.mark.parametrize("claim", ["advanced", "expert", "led", "managed", "fluent", "senior", "avancé", "confirmée"])
def test_level_and_scope_words_are_flagged_when_the_facts_do_not_support_them(claim: str) -> None:
    problems = check_text(f"I was {claim} in this work.", "Built a service", "line 1")
    assert any(p.kind == "claim" and p.term == claim.lower().replace("é", "e") for p in problems), problems


def test_a_level_word_the_facts_support_is_not_flagged() -> None:
    assert check_text("I am fluent in English.", "Fluent in English, C1.", "line 1") == []


# --- 4. typographic characters: the same text for the check and for the PDF --------------------------------


def test_typographic_characters_compare_as_their_plain_forms() -> None:
    assert check_text("the well‑known tool", "the well-known tool", "line 1") == []
    assert check_text("l’équipe", "l'équipe", "line 1") == []
    assert check_text("“data”", '"data"', "line 1") == []


def test_the_rendered_latex_uses_only_plain_characters_for_them() -> None:
    escaped = escape_latex("a b c‑d ’e’ “f”")
    assert escaped == "a~b c-d 'e' \"f\""
    assert not any(char in escaped for char in (" ", " ", " ", "‑", "’", "“"))


def test_a_rendered_cv_has_no_character_outside_the_glyphs_the_template_uses() -> None:
    cv = TailoredCV.model_validate(
        {"lines": [{"section": "experience", "text": "Built a service‑grade ’API’", "source_ids": ["exp-1"]}]}
    )
    source = render_cv(MASTER, cv)
    allowed = {0x0A} | set(range(0x20, 0x7F)) | set(range(0xA0, 0x100)) | {0x2013, 0x2014, 0x2026}
    unexpected = sorted({char for char in source if ord(char) not in allowed})
    assert unexpected == [], [hex(ord(c)) for c in unexpected]


def test_a_missing_glyph_reported_by_the_compiler_refuses_the_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Completed:
        returncode = 0
        stdout = ""
        stderr = "warning: Missing character: There is no ✓ (U+2713) in font lmroman10-regular!"

    def run(args: list[str], **kwargs: object) -> Completed:
        (tmp_path / "cv.pdf").write_bytes(b"%PDF-1.5 fake")
        return Completed()

    monkeypatch.setattr(compile_module.subprocess, "run", run)
    with pytest.raises(CVCompilationError, match="missing glyph"):
        compile_latex("\\documentclass{article}", tmp_path, Path("tectonic"), timeout_seconds=5)


# --- 5. mandatory parts, built by the code ----------------------------------------------------------------


def test_the_header_and_the_education_come_from_the_master_cv() -> None:
    source = render_cv(MASTER, CV)
    assert "Jane Doe" in source and "jane.doe@example.com" in source
    assert "\\section*{Education}" in source


def test_the_model_cannot_write_a_summary_or_an_education_line() -> None:
    for section in ("summary", "education"):
        with pytest.raises(ValidationError):
            TailoredCV.model_validate({"lines": [{"section": section, "text": "x", "source_ids": ["exp-1"]}]})


def test_a_master_cv_without_education_or_headline_or_email_has_mandatory_problems() -> None:
    raw = json.loads(json.dumps(SAMPLE_MASTER))
    raw["headline"] = ""
    raw["contact"]["email"] = ""
    raw["facts"] = [fact for fact in raw["facts"] if fact["kind"] != "education"]
    problems = verify_mandatory(MasterCV.model_validate(raw))
    terms = sorted(p.term for p in problems)
    assert terms == ["contact email", "education", "headline"]
    assert _kinds(problems) == {"mandatory"}


def test_mandatory_problems_are_never_sent_back_to_the_model() -> None:
    from linkedin_agent.graphs.application_graph import _revisable

    state = {"problems": [{"kind": "mandatory", "message": "no education", "location": "", "term": ""}]}
    assert _revisable(state) == []


# --- 6. structured problems, never truncated --------------------------------------------------------------


def test_the_refusal_message_counts_the_problems_and_never_truncates_them() -> None:
    problems = [{"kind": "claim", "location": "email body", "term": f"t{i}", "message": f"m{i}"} for i in range(7)]
    message = _refusal_message(problems)
    assert "7 grounding problems" in message
    assert "(and" not in message and "more)" not in message


def test_a_grounding_problem_carries_its_kind_location_and_term() -> None:
    problem = check_text("Led 40 people.", "Team of 5 people.", "line 2")[0]
    assert (
        problem.kind == "claim" or problem.kind == "number"
    )  # "Led" is a scope claim, and 40 is a number the cite lacks
    assert problem.location == "line 2"
    assert problem.as_dict()["term"] == problem.term != ""


def test_the_approval_refusal_lists_every_problem_in_the_response(
    client: TestClient,
    auth_headers: dict[str, str],
    answers: dict,  # noqa: F811
) -> None:
    _setup(client, auth_headers)
    answers["email"] = {
        "subject": "Application",
        "body": "I led a team of 40 engineers. I managed 3 teams and I am proficient in Docker. Expert in SQL.",
    }
    row = _prepare(client, auth_headers)
    stored = row["grounding_problems"]
    assert len(stored) >= 5
    response = _approve(client, auth_headers, row["id"])
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "cv_not_grounded"
    assert len(body["problems"]) == len(stored)  # every problem, not "and N more"
    assert all({"kind", "location", "term", "message"} <= set(p) for p in body["problems"])
    assert "(and" not in body["detail"]


# --- 7. the revision limit is said in the preparation status -------------------------------------------


def test_a_preparation_that_used_its_revisions_says_so_in_its_status(
    client: TestClient,
    auth_headers: dict[str, str],
    answers: dict,  # noqa: F811
) -> None:
    _setup(client, auth_headers)
    answers["email"] = {"subject": "Application", "body": "I led a team of 40 engineers."}
    run = client.post("/applications", json={**JOB, "recipient_email": "hr@acme.com"}, headers=auth_headers).json()
    status = client.get(f"/applications/preparations/{run['run_id']}", headers=auth_headers).json()
    assert status["status"] == "awaiting_review"
    assert status["grounding"]["revision_limit_reached"] is True
    assert status["grounding"]["problems"] >= 1
    assert "revisions are used up" in status["grounding"]["message"]


def test_a_clean_preparation_does_not_claim_a_revision_limit(
    client: TestClient,
    auth_headers: dict[str, str],
    answers: dict,  # noqa: F811
) -> None:
    _setup(client, auth_headers)
    run = client.post("/applications", json={**JOB, "recipient_email": "hr@acme.com"}, headers=auth_headers).json()
    status = client.get(f"/applications/preparations/{run['run_id']}", headers=auth_headers).json()
    assert status["grounding"] == {"problems": 0, "revision_limit_reached": False, "message": "No grounding problem."}
