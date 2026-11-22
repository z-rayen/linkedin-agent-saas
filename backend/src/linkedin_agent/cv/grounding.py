"""Grounding check: can every claim in the tailored CV and the application email be traced to the master CV?

Deterministic on purpose: no model judges the output. A text is checked against the facts it cites:
1. every cited id exists;
2. every number appears in the cited facts (or in the job context, for the email);
3. every capitalised term appears in the cited facts (or in the job context, for the email);
4. every technology name appears in the cited facts, in any case, accents folded;
5. every scope, level or degree claim (led, dirigé, fluent, courant, master) appears in the cited facts.

The job context (title, company) is allowed for numbers and proper terms only: it describes the job,
not the candidate. A technology or a claim taken from the job description is never accepted as a fact
about the candidate.

Known limits are documented as xfail cases in the tests: unlisted technologies, unlisted synonyms,
and meaning changes that reuse the same words.
"""

import re

from .lexicon import CLAIM_WORDS, NON_CLAIM_FOLDED, TECHNOLOGIES, fold
from .models import MasterCV, TailoredCV

NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
CAPITALISED = re.compile(r"\b[A-ZÀ-Ý][\w+#.\-]*[\w+#]\b")


def _normalise_number(value: str) -> str:
    return value.replace(",", ".")


def _word_pattern(word_folded: str) -> re.Pattern[str]:
    """Whole-word match that also works for terms ending in symbols, such as 'c++' or 'bac+5'."""
    return re.compile(rf"(?<![\w+#.]){re.escape(word_folded)}(?![\w+#])")


def _contains_word(haystack_folded: str, word: str) -> bool:
    return _word_pattern(fold(word)).search(haystack_folded) is not None


def capitalised_terms(text: str) -> list[str]:
    """The capitalised words of a text (proper nouns, job titles, acronyms), as written, without a final full stop."""
    return [term.rstrip(".") for term in CAPITALISED.findall(text)]


def contains_term(haystack: str, term: str) -> bool:
    """Whether `term` appears as a whole word in `haystack`. Case and accents are ignored on both sides."""
    return _contains_word(fold(haystack), term)


def check_text(text: str, cited_text: str, label: str, context_text: str = "") -> list[str]:
    """Problems in `text` that the `cited_text` (and, for numbers and proper terms, `context_text`) do not support."""
    cited_folded = fold(cited_text)
    context_folded = fold(context_text)
    cited_numbers = {_normalise_number(n) for n in NUMBER.findall(cited_text)}
    context_numbers = {_normalise_number(n) for n in NUMBER.findall(context_text)}
    text_folded = fold(text)
    problems: dict[str, None] = {}  # ordered set: the same problem is reported once

    for number in NUMBER.findall(text):
        normalised = _normalise_number(number)
        if normalised not in cited_numbers and normalised not in context_numbers:
            problems[f"{label}: number '{number}' does not appear in the cited facts"] = None

    for term in CAPITALISED.findall(text):
        term = term.rstrip(".")
        if fold(term) in NON_CLAIM_FOLDED:
            continue
        if not (_contains_word(cited_folded, term) or _contains_word(context_folded, term)):
            problems[f"{label}: term '{term}' does not appear in the cited facts"] = None

    for tech in sorted(TECHNOLOGIES):
        if _contains_word(text_folded, tech) and not _contains_word(cited_folded, tech):
            problems[f"{label}: technology '{tech}' does not appear in the cited facts"] = None

    for claim in sorted(CLAIM_WORDS):
        if _contains_word(text_folded, claim) and not _contains_word(cited_folded, claim):
            problems[f"{label}: claim '{claim}' (scope, level or degree) is not supported by the cited facts"] = None

    return list(problems)


def verify_grounding(cv: TailoredCV, master: MasterCV) -> list[str]:
    """Problems in the tailored CV. Every line, in every section, must cite facts that support it."""
    facts = master.facts_by_id()
    problems: dict[str, None] = {}
    for index, line in enumerate(cv.lines, start=1):
        label = f"line {index}"
        unknown = [source for source in line.source_ids if source not in facts]
        if unknown:
            problems[f"{label}: unknown source id(s) {', '.join(unknown)}"] = None
            continue
        cited = " ".join(facts[source].text for source in line.source_ids)
        for problem in check_text(line.text, cited, label):
            problems[problem] = None
    return list(problems)


def cited_text_of(cv: TailoredCV, master: MasterCV) -> str:
    """The text of every fact the tailored CV cites. This is what the email may be checked against."""
    facts = master.facts_by_id()
    ids = dict.fromkeys(source for line in cv.lines for source in line.source_ids)
    return " ".join(facts[source].text for source in ids if source in facts)


def verify_email(subject: str, body: str, cv: TailoredCV, master: MasterCV, job_context: str) -> list[str]:
    """Problems in the application email, checked against every fact the tailored CV cites.

    `job_context` (job title, company) is allowed for proper terms and numbers only.
    """
    return check_text(f"{subject}\n{body}", cited_text_of(cv, master), "email body", job_context)


def check_output(cv: TailoredCV, subject: str, body: str, master: MasterCV, job_context: str) -> list[str]:
    """Every problem in the candidate's output: the CV lines and the email, against the facts the CV cites.

    This is the single gate that the application graph, the approval service and the tests all use.
    """
    return verify_grounding(cv, master) + verify_email(subject, body, cv, master, job_context)
