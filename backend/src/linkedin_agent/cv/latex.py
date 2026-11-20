"""LaTeX rendering. Every value that comes from a model or from the user is escaped.

Nothing from the generated text is ever interpreted as LaTeX: a line containing "\\input{...}"
or "$" is rendered as plain text.
"""

from importlib import resources
from string import Template

from .models import MasterCV, Section, TailoredCV

_SPECIALS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}

_SECTION_TITLES: dict[Section, str] = {
    "summary": "Summary",
    "skill": "Skills",
    "experience": "Experience",
    "project": "Projects",
}


def escape_latex(text: str) -> str:
    """Make arbitrary text safe to place inside a LaTeX document as plain text."""
    return "".join(_SPECIALS.get(char, char) for char in text)


def _lines_of(cv: TailoredCV, section: Section) -> list[str]:
    return [escape_latex(line.text) for line in cv.lines if line.section == section]


def _bullets(items: list[str]) -> str:
    body = "\n".join(f"  \\item {item}" for item in items)
    return f"\\begin{{itemize}}[leftmargin=*]\n{body}\n\\end{{itemize}}"


def _block(section: Section, cv: TailoredCV) -> str:
    """A whole section, or an empty string when the tailored CV has nothing for it."""
    items = _lines_of(cv, section)
    if not items:
        return ""
    title = f"\\section*{{{_SECTION_TITLES[section]}}}"
    if section == "skill":
        return f"{title}\n{', '.join(items)}\n"
    if section == "summary":
        return f"{title}\n{' '.join(items)}\n"
    return f"{title}\n{_bullets(items)}\n"


def _contact_line(master: MasterCV) -> str:
    parts = [master.contact.email, master.contact.phone, master.contact.location, *master.contact.links]
    return " \\textbar{} ".join(escape_latex(part) for part in parts if part)


def load_template() -> Template:
    text = resources.files("linkedin_agent.cv").joinpath("templates/cv.tex").read_text(encoding="utf-8")
    return Template(text)


def render_cv(master: MasterCV, cv: TailoredCV) -> str:
    """The complete LaTeX source of the tailored CV."""
    return load_template().substitute(
        name=escape_latex(master.full_name),
        headline=escape_latex(master.headline),
        contact=_contact_line(master),
        summary_block=_block("summary", cv),
        skills_block=_block("skill", cv),
        experience_block=_block("experience", cv),
        projects_block=_block("project", cv),
    )
