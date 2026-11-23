"""The application email, assembled by the code. The model writes only the subject, the opening sentence and the closing
sentence. Everything else is built here:

- the greeting, the label of the facts and the sign-off, in the language of the application;
- the facts, quoted as written in a short bulleted list (never rephrased, never merged);
- the signature, from the master CV, so no placeholder such as "[Your Name]" can reach the inbox.

The grounding check reads the body back with the fixed lines removed (`without_fixed_lines`), so the greeting, the label
and the sign-off are never checked as claims of the model.
"""

import re
from collections.abc import Sequence
from typing import Literal

from .lexicon import fold
from .models import Fact, MasterCV, TailoredCV

Language = Literal["fr", "en"]
LANGUAGES: tuple[Language, ...] = ("fr", "en")

SIGNATURE_SEPARATOR = "\n\n"
PARAGRAPH_SEPARATOR = "\n\n"
CONTACT_SEPARATOR = " | "
BULLET = "- "
DEFAULT_LANGUAGE: Language = "en"
GREETINGS: dict[Language, str] = {"fr": "Madame, Monsieur,", "en": "Dear hiring team,"}
SIGN_OFFS: dict[Language, str] = {"fr": "Cordialement,", "en": "Best regards,"}
FACTS_LABELS: dict[Language, str] = {"fr": "Expérience pertinente :", "en": "Relevant experience:"}
# Only these fact kinds are quoted in an email. A summary is a paragraph of the CV, a skill is a single word.
EMAIL_FACT_KINDS: frozenset[str] = frozenset({"experience", "project"})
# The job title counts for more than one word of the text: a short title can be in one language while the offer is in
# another, and the offer is the better witness of the language the employer writes in.
TITLE_WEIGHT = 3

# Words that identify the language of a text. Folded. Ties and texts with no marker fall back to English.
_LANGUAGE_MARKERS: dict[Language, frozenset[str]] = {
    "fr": frozenset(
        {
            "stage", "stagiaire", "alternance", "alternant", "poste", "candidature", "ingenieur", "ingenieure",
            "chef", "developpeur", "developpeuse", "donnees", "apprenti", "apprentie", "le", "la", "les", "des",
            "du", "et", "pour", "avec", "nous", "vous", "dans", "une", "un", "sur", "est", "sont",
        }
    ),
    "en": frozenset(
        {
            "intern", "internship", "engineer", "position", "role", "application", "developer", "scientist",
            "analyst", "manager", "lead", "the", "and", "for", "with", "we", "you", "in", "are", "is", "our", "of",
            "a", "an",
        }
    ),
}  # fmt: skip
_TOKEN = re.compile(r"[^\W\d_][\w+#-]*")


def _markers_found(text: str) -> dict[Language, int]:
    words = _TOKEN.findall(fold(text))
    return {language: sum(word in markers for word in words) for language, markers in _LANGUAGE_MARKERS.items()}


def language_of(job_title: str, job_text: str = "") -> Language:
    """The language of an application, read from the job title and the job text (the text counts for more words than
    the title). English when neither names a language. Used only when the user has not chosen one."""
    title = _markers_found(job_title)
    text = _markers_found(job_text)
    scores = {language: TITLE_WEIGHT * title[language] + text[language] for language in LANGUAGES}
    return "fr" if scores["fr"] > scores["en"] else DEFAULT_LANGUAGE


def signature(master: MasterCV) -> str:
    """The name, then one contact line with the non-empty contact details of the master CV."""
    contact = [master.contact.email, master.contact.phone, master.contact.location, *master.contact.links]
    contact_line = CONTACT_SEPARATOR.join(part for part in contact if part.strip())
    return f"{master.full_name}\n{contact_line}".rstrip()


def sentence_of(text: str) -> str:
    """A fact as one sentence: quoted as written, with a full stop when it has no final punctuation."""
    stripped = text.strip()
    if stripped and stripped[-1] not in ".!?":
        return f"{stripped}."
    return stripped


def email_facts(cv: TailoredCV, master: MasterCV, limit: int) -> list[str]:
    """The facts of the email: the first `limit` experience or project facts that the tailored CV cites, in CV order.
    Each fact is quoted as written; no two facts are merged."""
    facts = master.facts_by_id()
    chosen: dict[str, Fact] = {}
    for line in cv.lines:
        for source in line.source_ids:
            fact = facts.get(source)
            if fact is not None and fact.kind in EMAIL_FACT_KINDS:
                chosen.setdefault(fact.id, fact)
    return [sentence_of(fact.text) for fact in list(chosen.values())[:limit]]


def build_body(
    language: Language, opening: str, closing: str, fact_sentences: Sequence[str], master: MasterCV
) -> str:
    """The whole email body: greeting, opening, the label and the bulleted facts, closing, sign-off, then the signature."""
    paragraphs = [GREETINGS[language]]
    if opening.strip():
        paragraphs.append(opening.strip())
    if fact_sentences:
        bullets = "\n".join(f"{BULLET}{sentence}" for sentence in fact_sentences)
        paragraphs.append(f"{FACTS_LABELS[language]}\n{bullets}")
    if closing.strip():
        paragraphs.append(closing.strip())
    paragraphs.append(SIGN_OFFS[language])
    return PARAGRAPH_SEPARATOR.join(paragraphs) + SIGNATURE_SEPARATOR + signature(master)


def with_signature(body: str, master: MasterCV) -> str:
    """The body the model wrote, followed by the signature. Adding it twice is impossible."""
    written = without_signature(body, master)
    return f"{written.rstrip()}{SIGNATURE_SEPARATOR}{signature(master)}"


def without_signature(body: str, master: MasterCV) -> str:
    """The body without the signature, if it ends with one. Used to check what the model actually wrote."""
    block = signature(master)
    stripped = body.rstrip()
    if stripped.endswith(block):
        return stripped[: -len(block)].rstrip()
    return body


def without_fixed_lines(body: str, master: MasterCV) -> str:
    """The body without the signature, the greeting, the label of the facts and the sign-off that the code adds, in any
    supported language. What remains is what the model wrote and the facts: the only text the grounding check reads."""
    text = without_signature(body, master).strip()
    for greeting in GREETINGS.values():
        if text.startswith(greeting):
            text = text[len(greeting) :].lstrip()
            break
    for label in FACTS_LABELS.values():
        text = text.replace(f"{label}\n", "", 1)
    for sign_off in SIGN_OFFS.values():
        if text.endswith(sign_off):
            text = text[: -len(sign_off)].rstrip()
            break
    return text
