"""Real Tectonic compilations of the CV. The PDF text is extracted with pypdf and checked.

Opt-in locally: set TEST_TECTONIC_PATH to the Tectonic binary. Without it the tests are skipped.
In CI, REQUIRE_TECTONIC=1 turns a missing binary into an error, so these tests cannot be skipped silently: the CI job
installs the pinned Tectonic (checksum-verified, see backend/docker/install_tectonic.py) and sets both variables.

The first run with a binary downloads the LaTeX packages it needs, so it needs the network.
"""

import os
import re
from pathlib import Path

import pytest
from pypdf import PdfReader

from linkedin_agent.core.exceptions import CVCompilationError
from linkedin_agent.core.settings import get_settings
from linkedin_agent.cv.compile import LOG_NAME, compile_latex
from linkedin_agent.cv.latex import render_cv
from linkedin_agent.cv.models import MasterCV, TailoredCV
from linkedin_agent.cv.pipeline import load_master_cv

SAMPLE_MASTER = Path(__file__).resolve().parents[1] / "samples" / "sample_master_cv.json"
TECTONIC_ENV = "TEST_TECTONIC_PATH"
REQUIRE_ENV = "REQUIRE_TECTONIC"
_tectonic_value = os.environ.get(TECTONIC_ENV, "").strip()
_tectonic_binary = Path(_tectonic_value) if _tectonic_value else None
_required = os.environ.get(REQUIRE_ENV, "") == "1"

if _required and (_tectonic_binary is None or not _tectonic_binary.is_file()):
    # Raised at collection: a CI run that did not install Tectonic fails here, instead of passing with zero tests run.
    raise RuntimeError(f"{REQUIRE_ENV}=1 but {TECTONIC_ENV} is not an existing Tectonic binary")

pytestmark = pytest.mark.skipif(
    _tectonic_binary is None or not _tectonic_binary.is_file(),
    reason=(
        f"skipped: {TECTONIC_ENV} is not set to an existing Tectonic binary. "
        f"Set {TECTONIC_ENV} to its full path to run these tests (see the README, section Development)."
    ),
)


def _binary() -> Path:
    assert _tectonic_binary is not None  # guaranteed by the skip marker
    return _tectonic_binary


def _compile(master: MasterCV, cv: TailoredCV, out_dir: Path) -> tuple[Path, str]:
    """The PDF path and the compiler's log, from a real compilation."""
    pdf = compile_latex(render_cv(master, cv), out_dir, _binary(), get_settings().tectonic_timeout_seconds)
    return pdf, (out_dir / LOG_NAME).read_text(encoding="utf-8")


def _pdf_text(pdf: Path) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(str(pdf)).pages)


def test_the_sample_cv_compiles_with_its_header_education_contact_and_lines(tmp_path: Path) -> None:
    master = load_master_cv(SAMPLE_MASTER)
    cv = TailoredCV.model_validate(
        {
            "lines": [
                {"section": "skill", "text": "Python", "source_ids": ["skill-py"]},
                {"section": "experience", "text": "Built a document question-answering service on FastAPI and PostgreSQL", "source_ids": ["exp-1"]},
                {"section": "experience", "text": "Reduced average answer latency by 35 percent by adding a reranking step", "source_ids": ["exp-2"]},
                {"section": "project", "text": "Open-source tool that generates tailored PDF resumes", "source_ids": ["proj-1"]},
            ]
        }
    )  # fmt: skip
    pdf, log = _compile(master, cv, tmp_path)

    assert pdf.read_bytes().startswith(b"%PDF")
    assert pdf.stat().st_size > 1000
    assert "Missing character" not in log
    text = _pdf_text(pdf)
    assert "Jane Doe" in text  # the header, from the master CV
    assert "Data Science, 2023" in text  # the education, from the master CV, not written by the model
    assert "jane.doe@example.com" in text  # the contact line, from the master CV
    assert "Anytown, Country" in text
    assert "Built a document question-answering service" in text  # the model's experience lines
    assert "Reduced average answer latency" in text
    assert "Python" in text


def test_typographic_characters_and_an_accented_name_render_correctly(tmp_path: Path) -> None:
    master = MasterCV.model_validate(
        {
            "full_name": "Zoé Lefèvre",
            "headline": "Ingénieur ML — Paris",
            "contact": {"email": "zoe.lefevre@example.com", "phone": "", "location": "Paris", "links": []},
            "facts": [
                {"id": "exp-1", "kind": "experience", "text": "Réduit le temps de réponse de 1 250 ms"},
                {"id": "edu-1", "kind": "education", "text": "Master en science des données, 2023"},
            ],
        }
    )
    cv = TailoredCV.model_validate(
        {
            "lines": [
                {
                    "section": "experience",
                    "text": "Réduit le temps de réponse de 1 250 ms avec une étape ‘rerank’ "
                    "et un “grand” lot non‑bloquant",
                    "source_ids": ["exp-1"],
                }
            ]
        }
    )
    pdf, log = _compile(master, cv, tmp_path)

    assert "Missing character" not in log
    text = _pdf_text(pdf)
    assert "Zoé Lefèvre" in text  # accents survive the font
    assert "non-bloquant" in text  # the non-breaking hyphen is rendered as a plain hyphen
    assert re.search(r"1\s?250 ms", text), text  # the narrow no-break space is rendered as a space
    assert "rerank" in text and "grand" in text  # the curly quotes are rendered as plain quotes


def test_a_character_the_font_lacks_is_refused_by_the_real_compiler(tmp_path: Path) -> None:
    master = load_master_cv(SAMPLE_MASTER)
    cv = TailoredCV.model_validate(
        {"lines": [{"section": "skill", "text": "Python ✓ validated", "source_ids": ["skill-py"]}]}
    )
    with pytest.raises(CVCompilationError, match="missing glyph"):
        _compile(master, cv, tmp_path)
    assert "Missing character" in (tmp_path / LOG_NAME).read_text(encoding="utf-8")
