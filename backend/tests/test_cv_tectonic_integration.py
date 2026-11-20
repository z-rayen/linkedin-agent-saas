"""Real Tectonic compilation. Opt-in: set TEST_TECTONIC_PATH to the Tectonic binary.

Without it the test is skipped, so the default run stays offline and fast. The first run with a real binary
downloads LaTeX packages. The binary is read only from this variable, never from backend/.env.
"""

import os
from pathlib import Path

import pytest

from linkedin_agent.core.settings import get_settings
from linkedin_agent.cv.compile import compile_latex
from linkedin_agent.cv.latex import render_cv
from linkedin_agent.cv.models import TailoredCV
from linkedin_agent.cv.pipeline import load_master_cv

SAMPLE_MASTER = Path(__file__).resolve().parents[1] / "samples" / "sample_master_cv.json"
TECTONIC_ENV = "TEST_TECTONIC_PATH"
_tectonic_value = os.environ.get(TECTONIC_ENV, "").strip()
_tectonic_binary = Path(_tectonic_value) if _tectonic_value else None

pytestmark = pytest.mark.skipif(
    _tectonic_binary is None or not _tectonic_binary.is_file(),
    reason=(
        f"skipped: {TECTONIC_ENV} is not set to an existing Tectonic binary. "
        f"Set {TECTONIC_ENV} to its full path to run this test (see the README, section Development)."
    ),
)


def test_sample_cv_compiles_to_a_pdf(tmp_path: Path) -> None:
    assert _tectonic_binary is not None  # guaranteed by the skip marker
    master = load_master_cv(SAMPLE_MASTER)
    cv = TailoredCV.model_validate(
        {
            "lines": [
                {"section": "summary", "text": master.facts[0].text, "source_ids": [master.facts[0].id]},
                {
                    "section": "experience",
                    "text": "Built a document question-answering service",
                    "source_ids": ["exp-1"],
                },
                {"section": "skill", "text": "Python", "source_ids": ["skill-py"]},
            ]
        }
    )
    source = render_cv(master, cv)
    pdf = compile_latex(source, tmp_path, _tectonic_binary, get_settings().tectonic_timeout_seconds)
    assert pdf.read_bytes().startswith(b"%PDF")
