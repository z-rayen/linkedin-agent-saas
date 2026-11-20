"""Compile LaTeX to PDF with Tectonic, in an isolated folder, with untrusted input.

The LaTeX comes from a language model, so it is treated as untrusted: Tectonic runs with
--untrusted, which disables features that can touch the system (shell escape, file writes).
"""

import logging
import shutil
import subprocess
from pathlib import Path

from ..core.exceptions import CVCompilationError
from ..core.settings import Settings

logger = logging.getLogger(__name__)
SOURCE_NAME = "cv.tex"
PDF_NAME = "cv.pdf"
LOG_NAME = "cv-compile.log"


def find_tectonic(settings: Settings) -> Path:
    """The configured binary, or whatever `tectonic` resolves to on PATH."""
    if settings.tectonic_path:
        candidate = Path(settings.tectonic_path)
        if candidate.is_file():
            return candidate
        raise CVCompilationError(f"TECTONIC_PATH points to a missing file: {candidate}")
    found = shutil.which("tectonic")
    if found is None:
        raise CVCompilationError(
            "Tectonic not found: set the TECTONIC_PATH variable (environment, or backend/.env for the app)"
        )
    return Path(found)


def compile_latex(source: str, out_dir: Path, tectonic: Path, timeout_seconds: float) -> Path:
    """Write `source` into `out_dir`, compile it, and return the PDF path. The log is kept on failure."""
    out_dir.mkdir(parents=True, exist_ok=True)
    tex_path = out_dir / SOURCE_NAME
    tex_path.write_text(source, encoding="utf-8")
    pdf_path = out_dir / PDF_NAME
    pdf_path.unlink(missing_ok=True)

    try:
        result = subprocess.run(
            [str(tectonic), "--untrusted", "--outdir", str(out_dir), str(tex_path)],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CVCompilationError(f"Tectonic timed out after {timeout_seconds:.0f}s") from exc

    if result.returncode != 0 or not pdf_path.is_file():
        (out_dir / LOG_NAME).write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
        logger.error("tectonic failed returncode=%s log=%s", result.returncode, out_dir / LOG_NAME)
        raise CVCompilationError(f"LaTeX compilation failed, see {out_dir / LOG_NAME}")
    return pdf_path
