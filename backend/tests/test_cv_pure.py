"""CV pipeline without LaTeX or network: escaping, rendering, grounding and master-file validation."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from linkedin_agent.cv.grounding import verify_grounding
from linkedin_agent.cv.latex import escape_latex, render_cv
from linkedin_agent.cv.models import Fact, MasterCV, TailoredCV
from linkedin_agent.cv.pipeline import load_master_cv

SAMPLE_MASTER = Path(__file__).resolve().parents[1] / "samples" / "sample_master_cv.json"


@pytest.fixture
def master() -> MasterCV:
    return load_master_cv(SAMPLE_MASTER)


def tailored(*lines: tuple[str, str, list[str]]) -> TailoredCV:
    return TailoredCV.model_validate({"lines": [{"section": s, "text": t, "source_ids": ids} for s, t, ids in lines]})


# --- escaping and rendering --------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "escaped"),
    [
        ("50%", r"50\%"),
        ("R&D", r"R\&D"),
        ("$5", r"\$5"),
        ("C#", r"C\#"),
        ("snake_case", r"snake\_case"),
        ("{x}", r"\{x\}"),
        ("a\\input{secret}", r"a\textbackslash{}input\{secret\}"),
        ("~ ^", r"\textasciitilde{} \textasciicircum{}"),
    ],
)
def test_latex_specials_are_escaped(raw: str, escaped: str) -> None:
    assert escape_latex(raw) == escaped


def test_model_output_cannot_inject_latex_commands(master: MasterCV) -> None:
    cv = tailored(("experience", r"\write18{rm -rf /} and $\alpha$", ["exp-1"]))
    source = render_cv(master, cv)
    assert r"\write18" not in source
    assert r"\textbackslash{}write18" in source


def test_rendered_document_has_no_unfilled_placeholders(master: MasterCV) -> None:
    cv = tailored(("summary", "Machine learning engineer", ["sum-1"]))
    source = render_cv(master, cv)
    assert "$" not in source.replace(r"\$", "")
    assert "\\begin{document}" in source and "\\end{document}" in source


def test_empty_sections_are_omitted(master: MasterCV) -> None:
    source = render_cv(master, tailored(("experience", "Built a service", ["exp-1"])))
    assert "\\section*{Experience}" in source
    assert "\\section*{Projects}" not in source
    assert "\\section*{Skills}" not in source


def test_contact_and_name_are_rendered(master: MasterCV) -> None:
    source = render_cv(master, tailored(("summary", "Engineer", ["sum-1"])))
    assert "Jane Doe" in source
    assert "jane.doe@example.com" in source


# --- grounding ---------------------------------------------------------------


def test_fully_grounded_line_passes(master: MasterCV) -> None:
    cv = tailored(("experience", "Built a document question-answering service on FastAPI", ["exp-1"]))
    assert verify_grounding(cv, master) == []


def test_invented_number_is_flagged(master: MasterCV) -> None:
    cv = tailored(("experience", "Served 5000 queries per day", ["exp-1"]))  # source says 2000
    problems = verify_grounding(cv, master)
    assert any("'5000'" in p for p in problems)


def test_invented_technology_is_flagged(master: MasterCV) -> None:
    cv = tailored(("experience", "Built the service on Kubernetes", ["exp-1"]))
    problems = verify_grounding(cv, master)
    assert any("'Kubernetes'" in p for p in problems)


def test_number_from_another_fact_does_not_count_as_cited(master: MasterCV) -> None:
    # "35" exists in exp-2, but the line cites exp-1 only.
    cv = tailored(("experience", "Reduced latency by 35 percent", ["exp-1"]))
    assert any("'35'" in p for p in verify_grounding(cv, master))


def test_unknown_source_id_is_flagged(master: MasterCV) -> None:
    cv = tailored(("experience", "Built a service", ["exp-404"]))
    assert any("unknown source id" in p for p in verify_grounding(cv, master))


def test_capitalised_common_words_are_not_claims(master: MasterCV) -> None:
    cv = tailored(("experience", "Built and Designed the service with Python", ["exp-1", "skill-py"]))
    assert verify_grounding(cv, master) == []


def test_french_and_english_openers_are_not_claims(master: MasterCV) -> None:
    cv = tailored(("experience", "Développé un service avec Python", ["exp-1", "skill-py"]))
    assert verify_grounding(cv, master) == []


def test_decimal_numbers_match_with_comma_or_dot() -> None:
    master = MasterCV(full_name="A", facts=[Fact(id="f1", kind="project", text="F1 score 0,87 on test set")])
    cv = tailored(("project", "F1 score of 0.87 on the test set", ["f1"]))
    assert verify_grounding(cv, master) == []


# --- master file validation --------------------------------------------------


def test_fact_id_must_be_url_and_latex_safe() -> None:
    with pytest.raises(ValidationError):
        Fact(id="bad id!", kind="skill", text="Python")


def test_master_cv_needs_at_least_one_fact() -> None:
    with pytest.raises(ValidationError):
        MasterCV(full_name="Someone", facts=[])


def test_tailored_line_needs_a_source() -> None:
    with pytest.raises(ValidationError):
        TailoredCV.model_validate({"lines": [{"section": "skill", "text": "Python", "source_ids": []}]})
