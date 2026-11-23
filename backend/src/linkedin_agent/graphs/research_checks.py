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
from ..cv.grounding import capitalised_terms, contains_term
from ..cv.lexicon import NON_CLAIM_FOLDED, fold

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
    dropped_entities: int  # names or titles that no cited source contains
    labelled: int
    cells_filled: int
    labels_removed: int  # snippet labels written by the model and removed by the check
    count_caution: bool  # figures come from two or more search pages: the caution must be shown

    @property
    def dropped_numbers(self) -> int:
        return self.dropped_numbers_not_found + self.dropped_numbers_snippet_only


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


def _label_pattern(labels: tuple[str, ...]) -> re.Pattern[str] | None:
    present = sorted({label for label in labels if label}, key=len, reverse=True)
    if not present:
        return None
    return re.compile("|".join(re.escape(label) for label in present), re.IGNORECASE)


# --- entities: proper nouns, company names and job titles must appear in the cited sources ------------------------

# Capitalised words that are not entities: sentence openers, articles, and words that appear in every result page.
ENTITY_COMMON: frozenset[str] = frozenset(
    fold(word)
    for word in (
        "A", "À", "Au", "De", "Des", "Du", "Et", "Ou", "En", "Il", "Elle", "Ils", "Ce", "Cette", "Ces", "Les", "Le",
        "La", "L", "D", "Un", "Une", "Pour", "Dans", "Avec", "Sans", "Sur", "Selon", "Plus", "Moins", "Non", "Oui",
        "The", "This", "These", "There", "And", "Or", "Of", "In", "On", "For", "With", "Not", "According", "Sources",
        "Source", "Results", "Result", "Search", "Snippet", "Full", "Page", "Note", "Caution", "Remarque", "Attention",
        "Offres", "Offre", "Jobs", "Job", "Emplois", "Count", "Location", "Title",
        # Sentence openers, English and French: they start sentences, they are not names.
        "It", "Its", "He", "She", "We", "They", "Their", "Our", "You", "Your", "I", "My", "His", "Her", "Them",
        "Based", "Here", "Overall", "Recent", "Recently", "Several", "Many", "Some", "Each", "Both", "Also", "Then",
        "After", "Before", "Because", "However", "Although", "Yes", "No", "Other", "Others", "Most", "Many", "Nearly",
        "Voici", "Ici", "Aucun", "Aucune", "Certaines", "Certains", "Plusieurs", "Beaucoup", "Chaque", "Également",
        "Ensuite", "Enfin", "Cependant", "Puis", "Donc", "Alors", "Mais", "Quels", "Quelles", "Quel", "Quelle", "Où",
        "Environ", "Parmi", "Depuis", "Lors", "Ainsi", "Aussi",
    )
)  # fmt: skip

# A line that holds only citations ("- [1]") asserts nothing: it is dropped.
CITATION_FRAGMENT = re.compile(r"\s*(?:[-*•]|\d+[.)])?\s*(?:\[\d{1,3}\]\s*)+")
MISSING_ENTITY = "missing_entity"
OK = "ok"


# Job titles end with a role word. A title is a phrase of two or three words ending with one of these, and it must
# appear in the cited sources like a name does: "Machine Learning Engineer" is checked, whatever its capitals.
ROLE_HEAD_WORDS: frozenset[str] = frozenset(
    fold(word)
    for word in (
        "engineer", "engineers", "scientist", "analyst", "developer", "researcher", "architect", "intern",
        "internship", "stagiaire", "ingénieur", "ingenieur", "chercheur", "développeur", "developpeur", "analyste",
        "alternant", "alternance",
    )
)  # fmt: skip
TITLE_STOP_WORDS: frozenset[str] = frozenset(
    fold(word)
    for word in (
        "a", "an", "the", "our", "your", "as", "and", "or", "of", "in", "at", "for", "with", "to", "un", "une", "le",
        "la", "les", "de", "du", "des", "au", "aux", "pour", "chez", "dans", "en", "et", "ou",
    )
)  # fmt: skip
MAX_TITLE_WORDS = 3
TITLE_WORD = re.compile(r"[^\W\d_][\w'’-]*")


def title_phrases_in(statement: str) -> list[str]:
    """The job titles a statement names: a modifier of one or two words followed by a role word. The words must be
    next to each other, separated by spaces only, so a title never borrows a word from the cell or clause before it."""
    text = CITATION_MARKER.sub(" ", statement)
    matches = list(TITLE_WORD.finditer(text))
    phrases: dict[str, str] = {}
    for index, match in enumerate(matches):
        if fold(match.group(0)) not in ROLE_HEAD_WORDS:
            continue
        first = index
        while (
            first > 0
            and index - first < MAX_TITLE_WORDS - 1
            and re.fullmatch(r"[ 	]+", text[matches[first - 1].end() : matches[first].start()])
            and fold(matches[first - 1].group(0)) not in TITLE_STOP_WORDS
            and fold(matches[first - 1].group(0)) not in ROLE_HEAD_WORDS
        ):
            first -= 1
        if index - first < 1:  # a bare role word ("an engineer") names no title
            continue
        phrase = text[matches[first].start() : match.end()]
        phrases.setdefault(fold(phrase), phrase)
    return list(phrases.values())


# A company written in lowercase ("at capgemini", "chez société générale") is found by the word that introduces it:
# the name is the one or two words after the preposition, when they are not common words. It is then an entity like a
# capitalised one, so the cited sources must contain it (case and accents aside).
COMPANY_INTRODUCER = re.compile(
    r"(?<!\w)(?:at|by|chez|par)\s+([^\W\d_][\w&'’.-]*)(?:\s+([^\W\d_][\w&'’.-]*))?",
    re.IGNORECASE,
)


def lowercase_companies_in(statement: str) -> list[str]:
    """Names written in lowercase after a company-introducing word, as written. Common words are not names."""
    found: list[str] = []
    text = CITATION_MARKER.sub(" ", statement)
    for match in COMPANY_INTRODUCER.finditer(text):
        first, second = match.group(1), match.group(2)
        if first[0].isupper() or first.isdigit() or _is_common(first):
            continue  # capitalised names are already entities; common words and numbers are not names
        name = first
        if second is not None and not _is_common(second):
            name = f"{first} {second}"
        found.append(name)
    return list(dict.fromkeys(found))


# Lowercase words that follow "at" or "by" in ordinary phrases ("at least", "by default"): never company names.
PHRASE_WORDS: frozenset[str] = frozenset(
    fold(word)
    for word in (
        "least", "most", "scale", "all", "once", "first", "present", "large", "default", "then", "hand", "design",
        "times", "random", "example", "its", "his", "her", "their", "our", "your", "one", "two", "night", "home",
        "best", "risk", "work", "best", "minimum", "maximum", "some", "various", "several", "many",
    )
)  # fmt: skip


def _is_common(word: str) -> bool:
    key = fold(word)
    return key in ENTITY_COMMON or key in NON_CLAIM_FOLDED or key in TITLE_STOP_WORDS or key in PHRASE_WORDS


def entities_in(statement: str) -> list[str]:
    """The distinct capitalised terms and job titles of a statement that its cited sources must contain, as written."""
    seen: dict[str, str] = {}
    for term in capitalised_terms(CITATION_MARKER.sub(" ", statement)):
        key = fold(term)
        if not key or key in ENTITY_COMMON or key in NON_CLAIM_FOLDED or term.isdigit():
            continue
        seen.setdefault(key, term)
    for title in title_phrases_in(statement):
        seen.setdefault(fold(title), title)
    for company in lowercase_companies_in(statement):
        seen.setdefault(fold(company), company)
    return list(seen.values())


@dataclass(frozen=True)
class Support:
    """What the cited sources of one statement contain, folded for comparison."""

    fetched_text: str
    any_text: str
    fetched_numbers: frozenset[str]
    any_numbers: frozenset[str]
    any_fetched: bool


def support_of(cited: list[int], evidence: list[Evidence], language: ResearchLanguage) -> Support:
    fetched = [evidence[number - 1] for number in cited if evidence[number - 1].fetched]
    everything = [evidence[number - 1] for number in cited]
    return Support(
        fetched_text=fold(" ".join(source.text for source in fetched)),
        any_text=fold(" ".join(source.text for source in everything)),
        fetched_numbers=frozenset(n for source in fetched for n in numbers_in(source.text, language)),
        any_numbers=frozenset(n for source in everything for n in numbers_in(source.text, language)),
        any_fetched=bool(fetched),
    )


@dataclass(frozen=True)
class Assessment:
    status: str  # OK, UNSUPPORTED (a figure is missing) or MISSING_ENTITY (a name or a title is missing)
    basis: str  # recomputed for the statement as it now reads: FETCHED, or SNIPPET when a token rests on a snippet
    missing_entities: tuple[str, ...]


def assess(statement: str, support: Support, language: ResearchLanguage) -> Assessment:
    """Whether every figure and every name of a statement is in its cited sources, and where each one is found."""
    numbers = numbers_in(statement, language)
    entities = entities_in(statement)
    if numbers - support.any_numbers:
        return Assessment(UNSUPPORTED, "", ())
    missing = tuple(term for term in entities if not contains_term(support.any_text, term))
    if missing:
        return Assessment(MISSING_ENTITY, "", missing)
    if not numbers and not entities:
        return Assessment(OK, FETCHED if support.any_fetched else SNIPPET, ())
    snippet_only = any(n not in support.fetched_numbers for n in numbers) or any(
        not contains_term(support.fetched_text, term) for term in entities
    )
    return Assessment(OK, SNIPPET if snippet_only else FETCHED, ())


def _count_dropped_figure(support: Support, counts: dict[str, int]) -> None:
    counts["not_found" if support.any_fetched else "snippet_only"] += 1


def _cell_citations(cell: str) -> str:
    return " ".join(match.group(0) for match in CITATION_MARKER.finditer(cell))


def _judge_row(
    row: str,
    cited: list[int],
    evidence: list[Evidence],
    language: ResearchLanguage,
    counts: dict[str, int],
) -> tuple[str, str] | None:
    """The row after its checks and its basis recomputed on the final row, or None when the whole row is removed.

    A cell that names a term no cited source contains gets the "not specified" wording, its citation kept. The row is
    then judged again as it now reads.
    """
    verdict = assess(row, support_of(cited, evidence, language), language)
    if verdict.status == UNSUPPORTED:
        _count_dropped_figure(support_of(cited, evidence, language), counts)
        return None
    if verdict.status == MISSING_ENTITY:
        cells = split_cells(row)
        for index, cell in enumerate(cells):
            hit = [term for term in verdict.missing_entities if contains_term(cell, term)]
            if hit:
                cells[index] = f"{language.not_specified} {_cell_citations(cell)}".strip()
                counts["entities"] += 1  # one removed cell
        row = join_cells(cells)
        cited = citations_in(row, len(evidence))
        verdict = assess(row, support_of(cited, evidence, language), language)
        if verdict.status != OK:
            return None
    if not cited and (numbers_in(row, language) or entities_in(row)):
        return None  # a figure or a name with no source: the row is not kept
    return row, verdict.basis


def check_answer(
    text: str,
    evidence: list[Evidence],
    *,
    language: ResearchLanguage,
    require_citations: bool,
    known_labels: tuple[str, ...] = (),
) -> AnswerCheck:
    """The answer, line by line, with every failing statement removed, corrected or labelled. Counts say what was done.

    `known_labels` are the snippet labels of every language: any of them written by the model is removed, and the
    label of a statement's basis is added by this function only, from the basis recomputed on the final statement.
    """
    label_pattern = _label_pattern((*known_labels, language.snippet_label))
    lines_out: list[str] = []
    claims: list[Claim] = []
    counts = {"uncited": 0, "relative": 0, "not_found": 0, "snippet_only": 0, "entities": 0}
    labelled = cells_filled = labels_removed = 0
    width: int | None = None  # the cell count of the table being read; its first row is the header

    all_lines = text.split("\n")
    for position, raw_line in enumerate(all_lines):
        line = raw_line
        if label_pattern is not None:
            line, removed_here = label_pattern.subn("", line)
            labels_removed += removed_here
            if removed_here:  # the punctuation that followed the label closes the sentence again
                line = re.sub(r"[ \t]+([.,;:!?])", r"\1", re.sub(r"[ \t]{2,}", " ", line)).rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            lines_out.append(line)  # blank lines and headings are structure
            width = None
            continue
        if CITATION_FRAGMENT.fullmatch(stripped):
            continue  # a line that is only citations asserts nothing
        if stripped.startswith("|"):
            if is_separator_row(stripped):
                lines_out.append(stripped)
                continue
            # A header is a row followed by the separator line. Without one, the first row still sets the width.
            followed_by_separator = position + 1 < len(all_lines) and is_separator_row(all_lines[position + 1].strip())
            is_header = width is None and followed_by_separator
            table_width = len(split_cells(stripped)) if width is None else width
            width = table_width
            row, filled = normalize_row(stripped, table_width, language.not_specified)
            cells_filled += filled
            if has_relative_time(row):
                counts["relative"] += 1
                continue
            if is_header:
                lines_out.append(row)  # the header names the columns: structure, not a statement
                continue
            cited = citations_in(row, len(evidence))
            if not cited and numbers_in(row, language):
                counts["uncited"] += 1
                continue
            outcome = _judge_row(row, cited, evidence, language, counts)
            if outcome is None:
                continue
            row, basis = outcome
            cited = citations_in(row, len(evidence))
            if not cited:
                lines_out.append(row)  # a body row with no citation and no figure or name: kept as structure
                continue
            if basis == SNIPPET:
                row = with_label_in_last_cell(row, language.snippet_label)
                labelled += 1
            claims.append(Claim(row, tuple(cited), basis))
            lines_out.append(row)
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
                counts["relative"] += 1
                continue
            cited = citations_in(sentence, len(evidence))
            if not cited:
                if language.not_specified.lower() in sentence.lower():
                    kept.append(sentence)  # an honest "not specified" is kept as it is
                    claims.append(Claim(sentence, (), NONE))
                elif require_citations or numbers_in(sentence, language) or entities_in(sentence):
                    # Without a citation, a figure or a name has no source: the sentence is not kept.
                    counts["uncited"] += 1
                else:
                    kept.append(sentence)
                    claims.append(Claim(sentence, (), NONE))
                continue
            support = support_of(cited, evidence, language)
            verdict = assess(sentence, support, language)
            if verdict.status == UNSUPPORTED:
                _count_dropped_figure(support, counts)
                continue
            if verdict.status == MISSING_ENTITY:
                counts["entities"] += 1  # one removed statement
                continue
            shown = sentence
            if verdict.basis == SNIPPET:
                shown = f"{sentence} {language.snippet_label}"
                labelled += 1
            kept.append(shown)
            claims.append(Claim(shown, tuple(cited), verdict.basis))
        if kept:
            lines_out.append(prefix + " ".join(kept))

    text_out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines_out)).strip()
    # Figures taken from two or more search pages: their counts are not comparable, and the reader must be told so.
    figure_sources = {n for claim in claims if numbers_in(claim.text, language) for n in claim.sources}
    return AnswerCheck(
        text=text_out,
        claims=claims,
        dropped_uncited=counts["uncited"],
        dropped_relative_time=counts["relative"],
        dropped_numbers_not_found=counts["not_found"],
        dropped_numbers_snippet_only=counts["snippet_only"],
        dropped_entities=counts["entities"],
        labelled=labelled,
        cells_filled=cells_filled,
        labels_removed=labels_removed,
        count_caution=len(figure_sources) >= 2,
    )


# --- search results -----------------------------------------------------------------------------


def query_terms(query: str, stopwords: frozenset[str]) -> set[str]:
    return {word for word in WORD.findall(query.lower()) if len(word) >= MIN_TERM_LENGTH and word not in stopwords}


def _matched(term: str, tokens: set[str]) -> bool:
    if term in tokens:
        return True
    if len(term) < PREFIX_MATCH_LENGTH:
        return False
    prefix = term[:PREFIX_MATCH_LENGTH]
    return any(len(token) >= PREFIX_MATCH_LENGTH and token[:PREFIX_MATCH_LENGTH] == prefix for token in tokens)


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
