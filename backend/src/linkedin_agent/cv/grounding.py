"""Grounding check: can every claim in the tailored CV and the application email be traced to the master CV?

Deterministic on purpose: no model judges the output. A text is checked against the facts it cites:
1. every cited id exists;
2. every number appears in the cited facts, compared by value ("2,000", "2000" and "2 000" are the same; "3" and "30"
   are not);
3. every capitalised term appears in the cited facts (or in the job context, for the email);
4. every technology name appears in the cited facts, in any case, accents folded;
5. every scope, level or degree claim (led, dirigé, proficient, courant, master) appears in the cited facts;
6. no unfilled placeholder remains ("[Your Name]", "{company}", "<role>").
7. in the email, no sentence combines words from two different facts (the facts quoted are verbatim);
8. scope words ("deploying", "designing", "hands-on", "optimization", "improving user experience") are claims
   unless a cited fact uses them.

The CV also has mandatory parts that come from the master CV and are never written by the model: a headline, a contact
email, and at least one education fact. A CV without them cannot be approved.

The job context (title, company) is allowed for numbers and proper terms only: it describes the job,
not the candidate. A technology or a claim taken from the job description is never accepted as a fact
about the candidate.

Every problem is a GroundingProblem: a message (the text a person reads), a kind, a location and the offending term.
Known limits are documented as xfail cases in the tests: unlisted technologies, unlisted synonyms,
and meaning changes that reuse the same words.
"""

import json
import re
from collections.abc import Sequence

from .email import without_fixed_lines
from .lexicon import CLAIM_WORDS, GENERIC_WORDS, NON_CLAIM_FOLDED, TECHNOLOGIES, fold
from .models import MasterCV, TailoredCV

# Digits grouped by spaces (regular, no-break, narrow no-break, thin), dots or commas, or a plain number with a decimal.
GROUPING = r"[ ,.   ]"
NUMBER = re.compile(rf"\d{{1,3}}(?:{GROUPING}\d{{3}})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")
CAPITALISED = re.compile(r"\b[A-ZÀ-Ý][\w+#.\-]*[\w+#]\b")
PLACEHOLDER = re.compile(r"\[[^\]\n]*\]|\{\{?[^}\n]*\}?\}|<[^>\n]{1,80}>")
MAX_PLACEHOLDER_CHARS = 80
WORD = re.compile(r"[^\W\d_][\w+#-]*")
MIN_CONTENT_WORD_LENGTH = 4  # shorter words are never a fact on their own
MIN_MIXED_WORDS = 2  # a sentence sharing one word with each of two facts is not a mix
SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")


class GroundingProblem(str):
    """One problem, as a string (the message a person reads) that also carries its kind, location and offending term.

    Existing callers keep working on the string. The API and the storage use `as_dict()`.
    """

    kind: str
    location: str
    term: str

    def __new__(cls, message: str, *, kind: str, location: str, term: str = "") -> "GroundingProblem":
        problem = super().__new__(cls, message)
        problem.kind = kind
        problem.location = location
        problem.term = term
        return problem

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "location": self.location, "term": self.term, "message": str(self)}


def _normalise_number(value: str) -> str:
    """The value of a number, as a canonical string. Grouping separators are removed; a decimal comma or point is kept.

    "2,000", "2000", "2 000" and "2.000" are one value; "2,5" and "2.5" are 2.5; "3" and "30" stay different.
    """
    compact = re.sub(r"[\s   ]", "", value)
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", compact):
        return str(int(re.sub(r"[.,]", "", compact)))
    grouped_with_decimals = re.fullmatch(r"(\d{1,3}(?:[.,]\d{3})+)[.,](\d+)", compact)
    if grouped_with_decimals:
        whole = int(re.sub(r"[.,]", "", grouped_with_decimals.group(1)))
        return f"{whole}.{grouped_with_decimals.group(2)}"
    return f"{float(compact.replace(',', '.')):.10g}"


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


def check_text(text: str, cited_text: str, label: str, context_text: str = "") -> list[GroundingProblem]:
    """Problems in `text` that the `cited_text` (and, for numbers and proper terms, `context_text`) do not support."""
    cited_folded = fold(cited_text)
    context_folded = fold(context_text)
    cited_numbers = {_normalise_number(n) for n in NUMBER.findall(cited_text)}
    context_numbers = {_normalise_number(n) for n in NUMBER.findall(context_text)}
    text_folded = fold(text)
    problems: dict[str, GroundingProblem] = {}  # ordered set: the same problem is reported once

    def add(message: str, kind: str, term: str) -> None:
        problems.setdefault(message, GroundingProblem(message, kind=kind, location=label, term=term))

    for placeholder in PLACEHOLDER.findall(text):
        add(f"{label}: unfilled placeholder '{placeholder}' must be replaced or removed", "placeholder", placeholder)

    for number in NUMBER.findall(text):
        normalised = _normalise_number(number)
        if normalised not in cited_numbers and normalised not in context_numbers:
            add(f"{label}: number '{number}' does not appear in the cited facts", "number", number)

    for term in CAPITALISED.findall(text):
        term = term.rstrip(".")
        if fold(term) in NON_CLAIM_FOLDED:
            continue
        if not (_contains_word(cited_folded, term) or _contains_word(context_folded, term)):
            add(f"{label}: term '{term}' does not appear in the cited facts", "term", term)

    for tech in sorted(TECHNOLOGIES):
        if _contains_word(text_folded, tech) and not _contains_word(cited_folded, tech):
            add(f"{label}: technology '{tech}' does not appear in the cited facts", "technology", tech)

    for claim in sorted(CLAIM_WORDS):
        if _contains_word(text_folded, claim) and not _contains_word(cited_folded, claim):
            add(
                f"{label}: claim '{claim}' (scope, level or degree) is not supported by the cited facts",
                "claim",
                claim,
            )

    return list(problems.values())


def content_words(text: str) -> set[str]:
    """The words of a text that can identify a fact: folded, long enough, and not generic."""
    return {
        word for word in WORD.findall(fold(text)) if len(word) >= MIN_CONTENT_WORD_LENGTH and word not in GENERIC_WORDS
    }


def sentences_of(text: str) -> list[str]:
    return [part.strip() for part in SENTENCE_END.split(text) if part.strip()]


def mixed_sentences(text: str, facts: Sequence[str], label: str, context_text: str = "") -> list[GroundingProblem]:
    """Sentences that combine words from two different facts.

    A sentence may use several words of one fact, or one word from each of two facts. It may not take its content
    from two facts at once, because the combination is a claim that no fact makes. A sentence is flagged when its
    words that appear in the facts are spread over two or more facts and no single fact contains all of them.
    The job context (title, company) is set aside: it describes the job, not the candidate.
    """
    context = content_words(context_text)
    fact_words = [content_words(fact) - context for fact in facts]
    known = set().union(*fact_words) if fact_words else set()
    problems: list[GroundingProblem] = []
    for sentence in sentences_of(text):
        matched = (content_words(sentence) - context) & known
        if len(matched) < MIN_MIXED_WORDS:
            continue
        contributing = [words for words in fact_words if words & matched]
        if len(contributing) < 2 or any(matched <= words for words in fact_words):
            continue
        terms = ", ".join(sorted(matched))
        message = f"{label}: the sentence '{sentence}' combines words from two different facts ({terms}): cite one fact"
        problems.append(GroundingProblem(message, kind="mixed_facts", location=label, term=terms))
    return problems


def verify_grounding(cv: TailoredCV, master: MasterCV) -> list[GroundingProblem]:
    """Problems in the tailored CV. Every line must cite facts that support it. A CV line may cite several facts (a
    project and the skill it used); the mixing check applies to the email sentences only, see verify_email."""
    facts = master.facts_by_id()
    problems: dict[str, GroundingProblem] = {}
    for index, line in enumerate(cv.lines, start=1):
        label = f"line {index}"
        unknown = [source for source in line.source_ids if source not in facts]
        if unknown:
            message = f"{label}: unknown source id(s) {', '.join(unknown)}"
            problems[message] = GroundingProblem(
                message, kind="unknown_source", location=label, term=", ".join(unknown)
            )
            continue
        cited_texts = [facts[source].text for source in line.source_ids]
        for problem in check_text(line.text, " ".join(cited_texts), label):
            problems[problem] = problem
    return list(problems.values())


def verify_mandatory(master: MasterCV) -> list[GroundingProblem]:
    """The parts the CV must have and that the code takes from the master CV: headline, contact email, education."""
    label = "master CV"
    missing: list[tuple[str, str]] = []
    if not master.headline.strip():
        missing.append(("headline is empty", "headline"))
    if not master.contact.email.strip():
        missing.append(("contact email is empty", "contact email"))
    if not any(fact.kind == "education" for fact in master.facts):
        missing.append(("no education fact", "education"))
    if not any(fact.kind == "summary" for fact in master.facts):
        missing.append(("no summary fact", "summary"))
    return [
        GroundingProblem(
            f"{label}: {reason}; the CV cannot be approved without it", kind="mandatory", location=label, term=term
        )
        for reason, term in missing
    ]


def verify_cv(cv: TailoredCV, master: MasterCV) -> list[GroundingProblem]:
    """Every problem of the CV: its lines, and the mandatory parts that the code adds from the master CV."""
    return verify_grounding(cv, master) + verify_mandatory(master)


def cited_fact_texts(cv: TailoredCV, master: MasterCV) -> list[str]:
    """The text of every fact the tailored CV cites, each once, in the order of the CV."""
    facts = master.facts_by_id()
    ids = dict.fromkeys(source for line in cv.lines for source in line.source_ids)
    return [facts[source].text for source in ids if source in facts]


def cited_text_of(cv: TailoredCV, master: MasterCV) -> str:
    """The text of every fact the tailored CV cites. This is what the email may be checked against."""
    return " ".join(cited_fact_texts(cv, master))


def verify_email(subject: str, body: str, cv: TailoredCV, master: MasterCV, job_context: str) -> list[GroundingProblem]:
    """Problems in the application email, checked against every fact the tailored CV cites.

    The greeting, the sign-off and the signature are built by the code, so they are not checked as claims; the rest of
    the body is: the model's sentences and the facts quoted in it. `job_context` (job title, company) is allowed for
    proper terms and numbers only, and is set aside when the sentences are checked for mixed facts.
    """
    written = without_fixed_lines(body, master)
    email_text = f"{subject}\n{written}"
    facts = cited_fact_texts(cv, master)
    return (
        check_text(email_text, " ".join(facts), "email body", job_context)
        + mixed_sentences(email_text, facts, "email body", job_context)
        + check_links_and_addresses(email_text, master)
    )


LINK = re.compile(r"(?:https?://|www\.)[^\s<>\"'()\[\]]+", re.IGNORECASE)
ADDRESS = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
TRAILING_PUNCTUATION = ".,;:!?"


def _link_key(token: str) -> str:
    """A link or an address as it is compared: no scheme, no "www.", no trailing slash or punctuation, lower case."""
    key = token.strip().rstrip(TRAILING_PUNCTUATION).lower()
    for prefix in ("https://", "http://", "www."):
        if key.startswith(prefix):
            key = key[len(prefix) :]
    return key.rstrip("/")


def check_links_and_addresses(text: str, master: MasterCV) -> list[GroundingProblem]:
    """Every link and every e-mail address in the text must be one the master CV contains: a contact detail, a link,
    or a fact. A model can invent a plausible address or a company page; that is refused, not corrected."""
    haystack = _link_key(json.dumps(master.model_dump(), ensure_ascii=False))
    problems: dict[str, GroundingProblem] = {}
    for token in LINK.findall(text) + ADDRESS.findall(text):
        key = _link_key(token)
        if not key or key in haystack:
            continue
        message = f"email body: the link or address '{token.rstrip(TRAILING_PUNCTUATION)}' is not in the master CV"
        problems.setdefault(
            message, GroundingProblem(message, kind="unknown_source", location="email body", term=token)
        )
    return list(problems.values())


def check_output(cv: TailoredCV, subject: str, body: str, master: MasterCV, job_context: str) -> list[GroundingProblem]:
    """Every problem in the candidate's output: the CV and the email, against the facts the CV cites.

    This is the single gate that the application graph, the approval service and the tests all use.
    """
    return verify_cv(cv, master) + verify_email(subject, body, cv, master, job_context)
