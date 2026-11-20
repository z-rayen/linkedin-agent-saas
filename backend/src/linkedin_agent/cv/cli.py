"""Try the CV pipeline from the command line, on your own master CV and a job description.

    python -m linkedin_agent.cv.cli --master ../data/private/master_cv.json --job job.txt --title "ML Engineer"

Prints the grounding problems (if any) and writes cv.pdf and cv.tex into the output folder.
Nothing is sent anywhere: this command only builds the document for you to review.
"""

import argparse
import asyncio
from pathlib import Path

from ..core.settings import get_settings
from .compile import compile_latex, find_tectonic
from .latex import render_cv
from .pipeline import load_master_cv, tailor_cv

DEFAULT_MASTER = "data/private/master_cv.json"
DEFAULT_OUT = "data/private/cv/preview"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a tailored CV PDF for review.")
    parser.add_argument("--master", default=DEFAULT_MASTER, type=Path, help="master CV JSON file")
    parser.add_argument("--job", required=True, type=Path, help="text file with the job description")
    parser.add_argument("--title", required=True, help="job title")
    parser.add_argument("--out", default=DEFAULT_OUT, type=Path, help="output folder")
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    master = load_master_cv(args.master)
    job_text = args.job.read_text(encoding="utf-8")

    result = await tailor_cv(args.title, job_text, master)
    tex_source = render_cv(master, result.cv)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "cv.tex").write_text(tex_source, encoding="utf-8")
    pdf = compile_latex(tex_source, args.out, find_tectonic(settings), settings.tectonic_timeout_seconds)

    print(f"PDF written to {pdf.resolve()}")
    if result.grounded:
        print("Grounding check: every line traces back to the master CV.")
        return 0
    print("Grounding check: NOT ready. These claims cannot be traced to the master CV:")
    for problem in result.problems:
        print(f"  - {problem}")
    return 2


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(_parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
