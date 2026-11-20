"""Build the attachment: the PDF compiled from one tailored CV, in a temporary folder that is deleted afterwards."""

import tempfile
from pathlib import Path

from ..core.settings import get_settings
from .compile import compile_latex, find_tectonic
from .latex import render_cv
from .models import MasterCV, TailoredCV


def build_cv_pdf(master: MasterCV, cv: TailoredCV) -> bytes:
    """The PDF bytes for exactly this CV. Raises CVCompilationError if Tectonic cannot produce it."""
    settings = get_settings()
    source = render_cv(master, cv)
    with tempfile.TemporaryDirectory(prefix="cv-build-") as folder:
        out_dir = Path(folder)
        pdf = compile_latex(source, out_dir, find_tectonic(settings), settings.tectonic_timeout_seconds)
        return pdf.read_bytes()
