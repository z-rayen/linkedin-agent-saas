"""Deterministic checks on the research answer and on the search results. No model is involved.

What happens to a statement that fails a check:

- no citation: the sentence is dropped (table rows and headings are kept, their numbers are still checked);
- a relative time ("last two weeks", "recently", "current"): the sentence is dropped, because its date cannot be
  checked against the sources;
- a number that does not appear, by value, in the text of the sources it cites: the sentence is dropped.
  "2,000" and "2000" are the same number; "7,497" and "7,479" are not;
- a statement whose cited sources are all search snippets (no full page was read): it gets the snippet label, so it is
  never presented as verified. A statement with at least one full page is not labelled;
- an empty table cell: it is filled with the "not specified" wording instead of a guess.

Citations are the other half: a [n] that names no shown source is removed by check_citations, before the rest runs.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

CITATION_MARKER = re.compile(r"\[(\d{1,3})\]")
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")
LIST_PREFIX = re.compile(r"^(\s*(?:[-*•]|\d+[.)])\s+)?(.*)$")
NUMBER = re.compile(r"\d{1,3}(?:[  .,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")
ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
THOUSANDS_GROUPED = re.compile(r"(\d{1,3}(?:[.,]\d{3})+)([.,]\d+)?")
EMPTY_CELLS = frozenset({"", "-", "—", "–", "?", "n/a", "N/A"})
WORD = re.compile(r"[a-zà-öø-ÿ0-9]+")
RELATIVE_TIME = re.compile(
    r"\b(?:"
    r"(?:last|past|previous)\s+(?:[\w\-–]+\s+)?(?:days?|weeks?|months?|years?|hours?)"
    r"|recently|recent|yesterday|today|tonight|ago|currently|current|latest|now|this\s+(?:week|month|year)"
    r"|il\s+y\s+a|hier|aujourd'hui|récemment|récents?|récentes?|derniers?|dernières?|cette\s+semaine|ce\s+mois"
    r")\b",
    re.IGNORECASE,
)
# Words that carry no topic: they are not counted when a search result is compared with the query.
STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "what", "which", "who", "how", "where", "when", "are", "was", "from", "about",
        "des", "les", "une", "pour", "dans", "avec", "quels", "quelles", "quel", "quelle", "est", "sur", "que", "qui",
        "sont", "entre", "sans", "par", "aux", "pas",
    }
)  # fmt: skip
PREFIX_MATCH_LENGTH = 5  # long enough terms also match on their first five letters: "retrieval" and "retrieve"


@dataclass(frozen=True)
class CitationCheck:
    text: str
    cited: list[int]  # valid numbers, in order of first use
    removed: int  # markers removed because they named no source that was shown


@dataclass(frozen=True)
class Evidence:
    """A source as the answer checks see it: the text it can be checked against, and whether a full page was read."""

    text: str
    fetched: bool


@dataclass(frozen=True)
class Claim:
    text: str
    sources: tuple[int, ...]
    basis: str  # "fetched", "snippet" (only snippets are cited) or "none" (uncited: table rows and headings only)


@dataclass(frozen=True)
class AnswerCheck:
    text: str
    claims: list[Claim]
    dropped_uncited: int
    dropped_relative_time: int
    dropped_numbers: int
    labelled: int
    cells_filled: int


FETCHED = "fetched"
SNIPPET = "snippet"
NONE = "none"


def normalize_number(token: str) -> float:
    """The value of a number as written: "2,000" and "2.000" are 2000, "2.5" and "2,5" are 2.5."""
    compact = token.replace(" ", "").replace(" ", "")
    grouped = THOUSANDS_GROUPED.fullmatch(compact)
    if grouped:
        whole = re.sub(r"[.,]", "", grouped.group(1))
        fraction = grouped.group(2)
        return float(whole + ("." + fraction[1:] if fraction else ""))
    return float(compact.replace(",", "."))


def _canonical(value: float) -> str:
    return f"{value:.10g}"


def numbers_in(text: str) -> set[str]:
    """The numbers and the ISO dates of a text, in canonical form. A date is one unit, not three numbers."""
    stripped = CITATION_MARKER.sub(" ", text)
    dates = {f"date:{match.group(0)}" for match in ISO_DATE.finditer(stripped)}
    rest = ISO_DATE.sub(" ", stripped)
    return dates | {_canonical(normalize_number(match.group(0))) for match in NUMBER.finditer(rest)}


def has_relative_time(text: str) -> bool:
    return RELATIVE_TIME.search(CITATION_MARKER.sub(" ", text)) is not None


def citations_in(text: str, source_count: int) -> list[int]:
    cited: list[int] = []
    for match in CITATION_MARKER.finditer(text):
        number = int(match.group(1))
        if 1 <= number <= source_count and number not in cited:
            cited.append(number)
    return cited


def check_citations(answer: str, source_count: int) -> CitationCheck:
    """Keep the [n] markers that name a shown source, remove the others. The text is otherwise unchanged."""
    cited: list[int] = []
    removed = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal removed
        number = int(match.group(1))
        if 1 <= number <= source_count:
            if number not in cited:
                cited.append(number)
            return match.group(0)
        removed += 1
        return ""

    text = CITATION_MARKER.sub(replace, answer)
    if removed:
        text = re.sub(r"[ \t]+([.,;:!?])", r"\1", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
    return CitationCheck(text=text.strip(), cited=cited, removed=removed)


def numbers_supported(text: str, cited: list[int], evidence: list[Evidence]) -> bool:
    """Every number of the text must appear, by value, in the text of at least one cited source."""
    needed = numbers_in(text)
    if not needed:
        return True
    available: set[str] = set()
    for number in cited:
        available |= numbers_in(evidence[number - 1].text)
    return needed <= available


def is_separator_row(row: str) -> bool:
    return "-" in row and re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", row) is not None


def fill_empty_cells(row: str, not_specified: str) -> tuple[str, int]:
    parts = row.strip().strip("|").split("|")
    filled = 0
    for index, part in enumerate(parts):
        if part.strip() in EMPTY_CELLS:
            parts[index] = f" {not_specified} "
            filled += 1
    return "| " + " | ".join(part.strip() for part in parts) + " |", filled


def check_answer(
    text: str,
    evidence: list[Evidence],
    *,
    snippet_label: str,
    not_specified: str,
    require_citations: bool,
) -> AnswerCheck:
    """The answer, line by line, with every failing statement removed or labelled. Counts say what was done."""
    lines_out: list[str] = []
    claims: list[Claim] = []
    dropped_uncited = dropped_relative = dropped_numbers = labelled = cells_filled = 0

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            lines_out.append(line)  # blank lines and headings are structure
            continue
        if stripped.startswith("|"):
            if is_separator_row(stripped):
                lines_out.append(stripped)
                continue
            row, filled = fill_empty_cells(stripped, not_specified)
            cells_filled += filled
            if has_relative_time(row):
                dropped_relative += 1
                continue
            cited = citations_in(row, len(evidence))
            if not numbers_supported(row, cited, evidence):
                dropped_numbers += 1
                continue
            if cited:  # a row that cites nothing is structure (a header, say): it is not a statement
                claims.append(Claim(row, tuple(cited), _basis(cited, evidence)))
            lines_out.append(row)
            continue

        prefix_match = LIST_PREFIX.match(line)
        prefix = (prefix_match.group(1) or "") if prefix_match else ""
        body = prefix_match.group(2) if prefix_match else line
        kept: list[str] = []
        for sentence in SENTENCE_BREAK.split(body.strip()):
            if not sentence:
                continue
            if has_relative_time(sentence):
                dropped_relative += 1
                continue
            cited = citations_in(sentence, len(evidence))
            if not cited:
                if not_specified.lower() in sentence.lower():
                    kept.append(sentence)  # an honest "not specified" is kept as it is
                    claims.append(Claim(sentence, (), NONE))
                elif numbers_in(sentence):
                    dropped_numbers += 1  # a number with no source is never kept
                elif require_citations:
                    dropped_uncited += 1
                else:
                    kept.append(sentence)
                    claims.append(Claim(sentence, (), NONE))
                continue
            if not numbers_supported(sentence, cited, evidence):
                dropped_numbers += 1
                continue
            if _basis(cited, evidence) == FETCHED:
                shown = sentence
                basis = FETCHED
            else:
                shown = f"{sentence} {snippet_label}"
                basis = SNIPPET
                labelled += 1
            kept.append(shown)
            claims.append(Claim(shown, tuple(cited), basis))
        if kept:
            lines_out.append(prefix + " ".join(kept))

    text_out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines_out)).strip()
    return AnswerCheck(
        text=text_out,
        claims=claims,
        dropped_uncited=dropped_uncited,
        dropped_relative_time=dropped_relative,
        dropped_numbers=dropped_numbers,
        labelled=labelled,
        cells_filled=cells_filled,
    )


def _basis(cited: list[int], evidence: list[Evidence]) -> str:
    return FETCHED if any(evidence[number - 1].fetched for number in cited) else SNIPPET


# --- search results -----------------------------------------------------------------------------


def query_terms(query: str) -> set[str]:
    return {word for word in WORD.findall(query.lower()) if len(word) >= 3 and word not in STOPWORDS}


def _same_term(term: str, token: str) -> bool:
    if token == term:
        return True
    return len(term) >= PREFIX_MATCH_LENGTH and len(token) >= PREFIX_MATCH_LENGTH and token[:5] == term[:5]


def relevance(query: str, title: str, summary: str, link: str) -> float:
    """The fraction of the query's terms found in the title, the summary or the domain of a search result."""
    terms = query_terms(query)
    if not terms:
        return 1.0  # a query with no topic word cannot be used to judge anything
    host = urlsplit(link).hostname or ""
    tokens = set(WORD.findall(f"{title} {summary} {host}".lower()))
    found = sum(1 for term in terms if any(_same_term(term, token) for token in tokens))
    return found / len(terms)
