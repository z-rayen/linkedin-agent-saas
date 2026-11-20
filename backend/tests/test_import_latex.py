"""LaTeX importer: it must reproduce what is written, never invent or normalise, and drafts must be refused."""

import json
from pathlib import Path

import pytest

from linkedin_agent.core.exceptions import ValidationFailedError
from linkedin_agent.cv.import_latex import convert, main, unwrap
from linkedin_agent.cv.pipeline import load_master_cv

SOURCE = r"""
\documentclass{article}
\newcommand{\name}{Jane Doe}
\name{Jane Doe}
\begin{document}
Contact: jane.doe@example.com
% \item This commented line must NOT become a fact
\section{Experience}
\resumeItem{Built a \textbf{document} service serving 2000 queries per day}
\item Reduced latency by 35\% with a reranking step
\section{Projects}
\item Open-source tool, see \href{https://example.com/tool}{the repository}
\section{Compétences}
\item Python, PostgreSQL
\section{Hobbies}
\item Climbing
\end{document}
"""


def test_items_are_kept_as_written_with_formatting_removed_only() -> None:
    master = convert(SOURCE).master
    texts = {fact["id"]: fact["text"] for fact in master["facts"]}  # type: ignore[index, union-attr]
    assert texts["experience-1"] == "Built a document service serving 2000 queries per day"
    assert texts["experience-2"] == "Reduced latency by 35% with a reranking step"
    assert texts["project-1"] == "Open-source tool, see the repository"
    assert texts["skill-1"] == "Python, PostgreSQL"


def test_numbers_and_levels_are_not_normalised() -> None:
    text = " ".join(fact["text"] for fact in convert(SOURCE).master["facts"])  # type: ignore[index, union-attr]
    assert "2000" in text and "35%" in text


def test_commented_items_never_become_facts() -> None:
    text = " ".join(fact["text"] for fact in convert(SOURCE).master["facts"])  # type: ignore[index, union-attr]
    assert "must NOT become" not in text


def test_unknown_section_is_skipped_and_reported() -> None:
    conversion = convert(SOURCE)
    assert "Climbing" not in " ".join(f["text"] for f in conversion.master["facts"])  # type: ignore[index, union-attr]
    assert any("Hobbies" in note for note in conversion.review)


def test_the_output_is_always_a_draft() -> None:
    assert convert(SOURCE).master["draft"] is True


def test_missing_name_is_marked_for_review_not_invented() -> None:
    conversion = convert("\\section{Experience}\n\\item Built a service\n")
    assert conversion.master["full_name"] == "REVIEW: full name"
    assert any("full name" in note for note in conversion.review)


def test_email_is_taken_from_the_source_only() -> None:
    assert convert(SOURCE).master["contact"]["email"] == "jane.doe@example.com"  # type: ignore[index]
    assert convert("\\section{Experience}\n\\item x\n").master["contact"]["email"] == ""  # type: ignore[index]


def test_unwrap_handles_nested_wrappers_and_escapes() -> None:
    assert unwrap(r"\textbf{\emph{bold italic}} 50\% \& co") == "bold italic 50% & co"


def test_every_fact_text_appears_in_the_source_after_unwrap() -> None:
    """Property: nothing in the output was written by the converter; each fact is a piece of the source."""
    plain = unwrap(SOURCE)
    for fact in convert(SOURCE).master["facts"]:  # type: ignore[union-attr, index]
        assert fact["text"] in plain


def test_draft_is_refused_by_the_loader(tmp_path: Path) -> None:
    path = tmp_path / "master_cv.json"
    path.write_text(json.dumps(convert(SOURCE).master), encoding="utf-8")
    with pytest.raises(ValidationFailedError, match="draft"):
        load_master_cv(path)


def test_reviewed_file_without_the_draft_key_loads(tmp_path: Path) -> None:
    data = convert(SOURCE).master
    data["full_name"] = "Jane Doe"
    del data["draft"]
    path = tmp_path / "master_cv.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert load_master_cv(path).full_name == "Jane Doe"


def test_api_refuses_a_draft_key_in_an_uploaded_master_cv() -> None:
    from pydantic import ValidationError

    from linkedin_agent.cv.models import MasterCV

    data = convert(SOURCE).master
    data["full_name"] = "Jane Doe"
    with pytest.raises(ValidationError):
        MasterCV.model_validate(data)  # "draft" is an unknown key: extra fields are forbidden


def test_cli_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    tex = tmp_path / "cv.tex"
    tex.write_text(SOURCE, encoding="utf-8")
    out = tmp_path / "master_cv.json"
    out.write_text("{}", encoding="utf-8")
    assert main([str(tex), "--out", str(out)]) == 1
    assert out.read_text(encoding="utf-8") == "{}"
    assert main([str(tex), "--out", str(out), "--force"]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["draft"] is True
