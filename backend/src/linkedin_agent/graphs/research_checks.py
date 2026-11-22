"""Deterministic checks on the research answer and on the search results. No model is involved.

What happens to a statement that fails a check:

- no citation: the sentence is dropped (table rows and headings are kept; their numbers are still checked);
- a relative time ("last two weeks", "recently", "current", "il y a", "récemment"...): the sentence is dropped, because
  its date cannot be checked against the sources. The answer says how many were dropped, and that this is not a
  statement that nothing was found;
- a number (or an ISO date) that does not appear, by value, in the text of a cited source: the sentence is dropped.
  The value of a number depends on the language of the answer: in English "2.500" is 2.5 and "2,500" is 2500; in
  French "2,500" is 2.5 and "2.500" is 2500. Spaces grouping thousands may be regular, no-break (U+00A0), narrow
  no-break (U+202F) or thin (U+2009). The text of a cited source is its title and its body (the page when it was read,
  the snippet otherwise). The reason is counted: the cited sources were all snippets, or a full page lacks the number;
- a statement whose number appears only in a search snippet is labelled as snippet-based, even when another cited
  source was read in full. A statement with no number rests on a full page when one of its sources was read;
- the snippet label is added by code only, from the basis. Any label written by the model is removed first, so a
  statement based on a full page never carries one;
- an empty table cell is filled with the "not specified" wording instead of a guess. A table row keeps the width of
  its table: a citation written after the last column is moved into the last cell, and a short row is padded.

Citations come first: check_citations removes every [n] that names no shown source, before the other checks run.

Search results are filtered by relevance (is_relevant). Its limits: it compares words, not meanings. Acronyms of three
or more letters ("NLP", "RAG", "SQL") match unrelated pages that contain the same letters; two-letter words such as
"ML" are ignored as query terms, so a query made only of them keeps every result.
"""

import re
from dataclasses import dataclass
from functools import cache
from urllib.parse import urlsplit

from ..core.settings import ResearchLanguage

CITATION_MARKER = re.compile(r"\[(\d{1,3})\]")
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")
LIST_PREFIX = re.compile(r"^(\s*(?:[-*•]|\d+[.)])\s+)?(.*)$")
ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
# Spaces that may group the digits of a number: regular, no-break, narrow no-break, thin.
GROUPING_SPACES = "    "
EMPTY_CELLS = frozenset({"", "-", "—", "–", "?", "n/a", "N/A"})
CITATION_CELL = re.compile(r"\s*(?:\[\d{1,3}\]\s*)+")
WORD = re.compile(r"[a-zà-öø-ÿ0-9]+")
MIN_TERM_LENGTH = 3  # shorter words ("ML", "UK", "de") are not topic words
PREFIX_MATCH_LENGTH = 5  # long terms also match on their first five letters: "retrieval" and "retrieve"
RELATIVE_TIME = re.compile(
    r"\b(?:"
    r"(?:last|past|previous)\s+(?:[\w\-–]+\s+)?(?:days?|weeks?|months?|years?|hours?)"
    r"|recently|recent|yesterday|today|tonight|ago|currently|current|latest|now|this\s+(?:week|month|year)"
    r"|il\s+y\s+a|hier|aujourd'hui|récemment|récents?|récentes?|derniers?|dernières?|cette\s+semaine|ce\s+mois"
    r"|ces\s+derniers?\s+(?:jours?|semaines?|mois)|dernières?\s+(?:jours?|semaines?|mois)"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CitationCheck:
    text: str
    cited: list[int]  # valid numbers, in order of first use
    removed: int  # markers removed because they named no source that was shown


@dataclass(frozen=True)
class Evidence:
    """A source as the answer checks see it: the text it can be checked against, and whether a full page was read.

    `text` is the title and the body (or the snippet): a figure in a title counts as a figure of the source.
    """

    text: str
    fetched: bool


@dataclass(frozen=True)
class Claim:
    text: str
    sources: tuple[int, ...]
    basis: str  # "fetched", "snippet" (labelled), or "none" (uncited: table rows and headings only)


@dataclass(frozen=True)
class AnswerCheck:
    text: str
    claims: list[Claim]
    dropped_uncited: int
    dropped_relative_time: int
    dropped_numbers_not_found: int  # cited full page(s) do not contain the number
    dropped_numbers_snippet_only: int  # only snippets are cited, and the number is not in them
    labelled: int
    cells_filled: int
    labels_removed: int  # snippet labels written by the model and removed by the check


FETCHED = "fetched"
SNIPPET = "snippet"
NONE = "none"
UNSUPPORTED = "unsupported"


@cache
def _number_pattern(decimal: str, thousands: str) -> re.Pattern[str]:
    d = re.escape(decimal)
    grouping = re.escape(GROUPING_SPACES + thousands)
    grouped = rf"\d{{1,3}}(?:[{grouping}]\d{{3}})+(?:{d}\d+)?"
    plain = rf"\d+(?:{d}\d+)?"
    return re.compile(f"{grouped}|{plain}")


def normalize_number(token: str, language: ResearchLanguage) -> str:
    """The canonical value of a number as the language writes it. "2.500" is 2500 in French and 2.5 in English."""
    compact = token
    for space in GROUPING_SPACES:
        compact = compact.replace(space, "")
    # Only grouped numbers contain a thousands separator, so removing it is safe for every token the pattern returns.
    compact = compact.replace(language.thousands_separator, "")
    compact = compact.replace(language.decimal_separator, ".")
    return f"{float(compact):.10g}"


def numbers_in(text: str, language: ResearchLanguage) -> set[str]:
    """The numbers and ISO dates of a text, canonical. A date is one unit: 2026-11-11 is not 2026, 11 and 11."""
    stripped = CITATION_MARKER.sub(" ", text)
    dates = {f"date:{match.group(0)}" for match in ISO_DATE.finditer(stripped)}
    rest = ISO_DATE.sub(" ", stripped)
    pattern = _number_pattern(language.decimal_separator, language.thousands_separator)
    return dates | {normalize_number(match.group(0), language) for match in pattern.finditer(rest)}


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


def number_basis(text: str, cited: list[int], evidence: list[Evidence], language: ResearchLanguage) -> str | None:
    """Where the numbers of a statement are found: in a full page (FETCHED), only in snippets (SNIPPET), or nowhere.

    None when the statement has no number. A number found in a snippet only is SNIPPET even when another cited source
    was read in full: the figure itself is what must be verified.
    """
    needed = numbers_in(text, language)
    if not needed:
        return None
    in_pages: set[str] = set()
    in_any: set[str] = set()
    for number in cited:
        source = evidence[number - 1]
        found = numbers_in(source.text, language)
        in_any |= found
        if source.fetched:
            in_pages |= found
    if needed <= in_pages:
        return FETCHED
    if needed <= in_any:
        return SNIPPET
    return UNSUPPORTED


def is_separator_row(row: str) -> bool:
    return "-" in row and re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", row) is not None


def split_cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def join_cells(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def normalize_row(row: str, width: int, not_specified: str) -> tuple[str, int]:
    """The row with exactly `width` cells. A citation written after the last column moves into the last cell; a short
    row is padded; an empty cell gets the "not specified" wording. Returns the row and the number of cells filled."""
    cells = split_cells(row)
    while len(cells) > width and len(cells) > 1 and CITATION_CELL.fullmatch(cells[-1]):
        citation = cells.pop()
        cells[-1] = f"{cells[-1]} {citation}".strip()
    cells.extend([""] * (width - len(cells)))
    filled = 0
    for index, cell in enumerate(cells):
        if cell in EMPTY_CELLS:
            cells[index] = not_specified
            filled += 1
    return join_cells(cells), filled


def fill_empty_cells(row: str, not_specified: str) -> tuple[str, int]:
    return normalize_row(row, len(split_cells(row)), not_specified)


def with_label_in_last_cell(row: str, label: str) -> str:
    """The row with the label inside its last cell, so that the row keeps its cells."""
    cells = split_cells(row)
    cells[-1] = f"{cells[-1]} {label}".strip()
    return join_cells(cells)


def _fallback_basis(cited: list[int], evidence: list[Evidence]) -> str:
    return FETCHED if any(evidence[number - 1].fetched for number in cited) else SNIPPET


def _label_pattern(labels: tuple[str, ...]) -> re.Pattern[str] | None:
    present = sorted({label for label in labels if label}, key=len, reverse=True)
    if not present:
        return None
    return re.compile("|".join(re.escape(label) for label in present), re.IGNORECASE)


def check_answer(
    text: str,
    evidence: list[Evidence],
    *,
    language: ResearchLanguage,
    require_citations: bool,
    known_labels: tuple[str, ...] = (),
) -> AnswerCheck:
    """The answer, line by line, with every failing statement removed or labelled. Counts say what was done.

    `known_labels` are the snippet labels of every language: any of them written by the model is removed, and the
    label of the statement's basis is added by this function only.
    """
    label_pattern = _label_pattern((*known_labels, language.snippet_label))
    lines_out: list[str] = []
    claims: list[Claim] = []
    dropped_uncited = dropped_relative = not_found = snippet_only = labelled = cells_filled = labels_removed = 0
    width: int | None = None  # the cell count of the table being read: its first row sets it

    def judge(statement: str, cited: list[int]) -> tuple[str, str] | None:
        """(basis, statement) for a statement with citations, or None when a number has no source (counted)."""
        nonlocal not_found, snippet_only
        verdict = number_basis(statement, cited, evidence, language)
        if verdict == UNSUPPORTED:
            if any(evidence[number - 1].fetched for number in cited):
                not_found += 1
            else:
                snippet_only += 1
            return None
        return (verdict or _fallback_basis(cited, evidence)), statement

    for raw_line in text.split("\n"):
        line = raw_line
        if label_pattern is not None:
            line, removed_here = label_pattern.subn("", line)
            labels_removed += removed_here
            if removed_here:
                line = re.sub(r"[ \t]{2,}", " ", line).rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            lines_out.append(line)  # blank lines and headings are structure
            width = None
            continue
        if stripped.startswith("|"):
            if is_separator_row(stripped):
                lines_out.append(stripped)
                continue
            if width is None:
                width = len(split_cells(stripped))
            row, filled = normalize_row(stripped, width, language.not_specified)
            cells_filled += filled
            if has_relative_time(row):
                dropped_relative += 1
                continue
            cited = citations_in(row, len(evidence))
            if not cited:
                # A row that cites nothing is structure (a header): it is kept, but it must not carry a number.
                if numbers_in(row, language):
                    dropped_uncited += 1
                    continue
                lines_out.append(row)
                continue
            judged = judge(row, cited)
            if judged is None:
                continue
            basis, shown = judged
            if basis == SNIPPET:
                shown = with_label_in_last_cell(shown, language.snippet_label)
                labelled += 1
            claims.append(Claim(shown, tuple(cited), basis))
            lines_out.append(shown)
            continue

        width = None  # a line that is not a table row ends the table
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
                if language.not_specified.lower() in sentence.lower():
                    kept.append(sentence)  # an honest "not specified" is kept as it is
                    claims.append(Claim(sentence, (), NONE))
                elif numbers_in(sentence, language):
                    dropped_uncited += 1  # a number with no source is never kept
                elif require_citations:
                    dropped_uncited += 1
                else:
                    kept.append(sentence)
                    claims.append(Claim(sentence, (), NONE))
                continue
            judged = judge(sentence, cited)
            if judged is None:
                continue
            basis, shown = judged
            if basis == SNIPPET:
                shown = f"{shown} {language.snippet_label}"
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
        dropped_numbers_not_found=not_found,
        dropped_numbers_snippet_only=snippet_only,
        labelled=labelled,
        cells_filled=cells_filled,
        labels_removed=labels_removed,
    )


# --- search results -----------------------------------------------------------------------------


def query_terms(query: str, stopwords: frozenset[str]) -> set[str]:
    return {word for word in WORD.findall(query.lower()) if len(word) >= MIN_TERM_LENGTH and word not in stopwords}


@cache
def _prefix_tokens(tokens: frozenset[str]) -> frozenset[str]:
    return frozenset(token[:PREFIX_MATCH_LENGTH] for token in tokens if len(token) >= PREFIX_MATCH_LENGTH)


def _matched(term: str, tokens: set[str]) -> bool:
    if term in tokens:
        return True
    if len(term) < PREFIX_MATCH_LENGTH:
        return False
    return term[:PREFIX_MATCH_LENGTH] in _prefix_tokens(frozenset(tokens))


def is_relevant(
    query: str,
    title: str,
    summary: str,
    link: str,
    *,
    stopwords: frozenset[str],
    min_share: float,
    min_title_matches: int,
) -> bool:
    """A search result is relevant when it matches enough of the query's topic words, and enough of them in its title
    or domain. A query without topic words cannot judge anything, so every result is kept."""
    terms = query_terms(query, stopwords)
    if not terms:
        return True
    host = urlsplit(link).hostname or ""
    title_tokens = set(WORD.findall(f"{title} {host}".lower()))
    all_tokens = title_tokens | set(WORD.findall(summary.lower()))
    matched_all = sum(1 for term in terms if _matched(term, all_tokens))
    matched_title = sum(1 for term in terms if _matched(term, title_tokens))
    required_title = min(min_title_matches, len(terms))
    return matched_all / len(terms) >= min_share and matched_title >= required_title


def detect_language(text: str, languages: dict[str, ResearchLanguage], default: str) -> str:
    """The language whose stopwords the text uses most. A tie, or no stopword at all, gives the default."""
    words = WORD.findall(text.lower())
    scores = {code: sum(1 for word in words if word in set(pack.stopwords)) for code, pack in languages.items()}
    best = max(scores.values(), default=0)
    if best == 0:
        return default
    leaders = [code for code, score in scores.items() if score == best]
    return default if default in leaders else leaders[0]
