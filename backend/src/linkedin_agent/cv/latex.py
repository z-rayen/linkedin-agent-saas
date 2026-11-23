"""LaTeX rendering. Every value that comes from a model or from the user is escaped.

Nothing from the generated text is ever interpreted as LaTeX: a line containing "\\input{...}"
or "$" is rendered as plain text.

The header (name, headline, contact line), the summary and the education come from the master CV, by code. The model
only selects and orders the skills, the experience and the projects. The section titles follow the language of the
application.

Typographic characters (no-break and narrow spaces, non-breaking hyphens, curly quotes) are replaced by the characters
the grounding check compares them with, so the rendered text and the checked text are the same text.
"""

from importlib import resources
from string import Template

from .email import Language
from .lexicon import TYPOGRAPHIC_EQUIVALENTS
from .models import Fact, MasterCV, Section, TailoredCV, TailoredLine

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
NO_BREAK_SPACE = "\u00a0"  # a LaTeX tie, written as "~" after escaping: it is not passed through _SPECIALS
_TYPOGRAPHY = {key: value for key, value in TYPOGRAPHIC_EQUIVALENTS.items() if key != NO_BREAK_SPACE}

# Section titles per language. The keys are the sections the model chooses, plus the parts the code adds.
TITLES: dict[Language, dict[str, str]] = {
    "en": {
        "summary": "Summary",
        "education": "Education",
        "skill": "Skills",
        "experience": "Experience",
        "project": "Projects",
    },
    "fr": {
        "summary": "Résumé",
        "education": "Formation",
        "skill": "Compétences",
        "experience": "Expérience",
        "project": "Projets",
    },
}


def escape_latex(text: str) -> str:
    """Make arbitrary text safe to place inside a LaTeX document as plain text."""
    pieces: list[str] = []
    for char in text:
        if char == NO_BREAK_SPACE:
            pieces.append("~")
        elif char in _SPECIALS:
            pieces.append(_SPECIALS[char])
        else:
            pieces.append(_TYPOGRAPHY.get(char, char))
    return "".join(pieces)


def _cites_summary(line: TailoredLine, facts: dict[str, Fact]) -> bool:
    """A line based on a summary fact repeats the summary, which the code writes from the master CV. It is not shown."""
    return any(facts[source].kind == "summary" for source in line.source_ids if source in facts)


def _lines_of(cv: TailoredCV, section: Section, facts: dict[str, Fact]) -> list[str]:
    return [
        escape_latex(line.text)
        for line in cv.lines
        if line.section == section and not _cites_summary(line, facts)
    ]


def _bullets(items: list[str]) -> str:
    body = "\n".join(f"  \\item {item}" for item in items)
    return f"\\begin{{itemize}}[leftmargin=*]\n{body}\n\\end{{itemize}}"


def _block(section: Section, cv: TailoredCV, facts: dict[str, Fact], titles: dict[str, str]) -> str:
    """A whole section, or an empty string when the tailored CV has nothing for it."""
    items = _lines_of(cv, section, facts)
    if not items:
        return ""
    title = f"\\section*{{{titles[section]}}}"
    if section == "skill":
        return f"{title}\n{', '.join(items)}\n"
    return f"{title}\n{_bullets(items)}\n"


def _summary_block(master: MasterCV, titles: dict[str, str]) -> str:
    """Every summary fact of the master CV, by code, as one paragraph under the header. Never a bullet."""
    texts = [escape_latex(fact.text) for fact in master.facts if fact.kind == "summary"]
    if not texts:
        return ""
    return f"\\section*{{{titles['summary']}}}\n{' '.join(texts)}\n"


def _education_block(master: MasterCV, titles: dict[str, str]) -> str:
    """Every education fact of the master CV, by code. Empty only when the master CV has none: approval then refuses."""
    items = [escape_latex(fact.text) for fact in master.facts if fact.kind == "education"]
    if not items:
        return ""
    return f"\\section*{{{titles['education']}}}\n{_bullets(items)}\n"


def _contact_line(master: MasterCV) -> str:
    parts = [master.contact.email, master.contact.phone, master.contact.location, *master.contact.links]
    return " \\textbar{} ".join(escape_latex(part) for part in parts if part)


def load_template() -> Template:
    text = resources.files("linkedin_agent.cv").joinpath("templates/cv.tex").read_text(encoding="utf-8")
    return Template(text)


def render_cv(master: MasterCV, cv: TailoredCV, language: Language = "en") -> str:
    """The complete LaTeX source of the tailored CV, with the section titles of the given language."""
    facts = master.facts_by_id()
    titles = TITLES[language]
    return load_template().substitute(
        name=escape_latex(master.full_name),
        headline=escape_latex(master.headline),
        contact=_contact_line(master),
        summary_block=_summary_block(master, titles),
        education_block=_education_block(master, titles),
        skills_block=_block("skill", cv, facts, titles),
        experience_block=_block("experience", cv, facts, titles),
        projects_block=_block("project", cv, facts, titles),
    )
