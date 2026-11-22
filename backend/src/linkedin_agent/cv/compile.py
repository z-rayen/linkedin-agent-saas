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
MISSING_GLYPH = "Missing character"  # the warning Tectonic prints for a character absent from the font


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


def ensure_tectonic_at_startup(settings: Settings) -> None:
    """Fail the API at startup, with a clear message, when CV compilation cannot work.

    Without this check the first CV request would be the one to discover a wrong TECTONIC_PATH. A Windows path
    inside the Linux container is the typical cause; the message names the variable to fix.
    """
    if not settings.tectonic_required:
        return
    try:
        binary = find_tectonic(settings)
    except CVCompilationError as exc:
        raise RuntimeError(
            f"API not started: {exc.message}. Tailored CVs cannot be compiled without Tectonic. "
            "Fix TECTONIC_PATH, or set TECTONIC_REQUIRED=false if you do not need CV PDFs."
        ) from None
    logger.info("tectonic available for CV compilation: %s", binary.name)


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
            encoding="utf-8",
            errors="replace",  # compiler messages may hold any character: never fail while reading them
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CVCompilationError(f"Tectonic timed out after {timeout_seconds:.0f}s") from exc

    # The compiler's output is kept on every run: it is the evidence that the PDF has no missing glyph.
    (out_dir / LOG_NAME).write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
    if result.returncode != 0 or not pdf_path.is_file():
        logger.error("tectonic failed returncode=%s log=%s", result.returncode, out_dir / LOG_NAME)
        raise CVCompilationError(f"LaTeX compilation failed, see {out_dir / LOG_NAME}")
    # A character the font does not contain is printed as a blank: the PDF would look right and read wrong. Refuse it.
    missing = [line.strip() for line in (result.stdout + "\n" + result.stderr).splitlines() if MISSING_GLYPH in line]
    if missing:
        logger.error("tectonic reported %d missing glyph(s) log=%s", len(missing), out_dir / LOG_NAME)
        raise CVCompilationError(
            f"the PDF would have {len(missing)} missing glyph(s), first: {missing[0]}. "
            "Replace the character in the source text."
        )
    return pdf_path
