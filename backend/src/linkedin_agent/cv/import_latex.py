"""Convert a LaTeX CV into a DRAFT master_cv.json, for you to review fact by fact.

The converter never invents or normalises content. It takes the text of each \\item or \\resumeItem
as written, removes only the formatting commands around it (bold, italics, links, escaped characters),
and keeps every number, date and level exactly as typed. Anything it cannot read is kept raw and listed
in the review file, so nothing disappears silently.

The output carries "draft": true. The application refuses a draft master CV until you have reviewed
every fact and removed that key.

    python -m linkedin_agent.cv.import_latex ../data/private/my_cv.tex --out ../data/private/master_cv.json
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

SECTION = re.compile(r"\\(?:section|cvsection|resumeSection)\*?\{([^}]*)\}")
ITEM_PREFIX = re.compile(r"^\s*\\(?:item|resumeItem|cvitem)\b\s*")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
NAME_PATTERNS = (re.compile(r"\\name\{([^}]*)\}"), re.compile(r"\\author\{([^}]*)\}"))
ESCAPED = {r"\%": "%", r"\&": "&", r"\$": "$", r"\#": "#", r"\_": "_"}
WRAPPERS = re.compile(r"\\(?:textbf|textit|emph|textsc|underline)\{([^{}]*)\}")
HREF = re.compile(r"\\href\{[^{}]*\}\{([^{}]*)\}")
SECTION_KINDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("experience", ("experience", "expérience", "experiences", "expériences", "work", "emploi", "stage", "internship")),
    ("project", ("project", "projet")),
    ("education", ("education", "éducation", "formation", "etudes", "études", "academic")),
    ("skill", ("skill", "compétence", "competence", "technolog", "language", "langue", "outil")),
    ("summary", ("summary", "profil", "about", "objectif", "profile")),
)


@dataclass
class Conversion:
    master: dict[str, object]
    review: list[str] = field(default_factory=list)


def _strip_comment(line: str) -> str:
    """Remove an unescaped % comment. An escaped \\% is content, not a comment."""
    return re.sub(r"(?<!\\)%.*", "", line)


def _balanced_argument(text: str) -> str | None:
    """The content of the first {...} group in `text`, with nested braces kept."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : index]
    return None


def unwrap(text: str) -> str:
    """Remove formatting commands only. Words, numbers and punctuation are left exactly as typed."""
    previous = None
    while previous != text:
        previous = text
        text = WRAPPERS.sub(r"\1", text)
        text = HREF.sub(r"\1", text)
    for escaped, plain in ESCAPED.items():
        text = text.replace(escaped, plain)
    return text.strip()


def _section_kind(title: str) -> str | None:
    folded = title.lower()
    for kind, keywords in SECTION_KINDS:
        if any(keyword in folded for keyword in keywords):
            return kind
    return None


def _item_text(line: str) -> str | None:
    match = ITEM_PREFIX.match(line)
    if match is None:
        return None
    rest = line[match.end() :]
    if line.lstrip().startswith(("\\resumeItem", "\\cvitem")):
        argument = _balanced_argument(rest)
        return argument if argument is not None else ""
    return rest.rstrip()


def convert(source: str) -> Conversion:
    review: list[str] = []
    lines = [_strip_comment(line) for line in source.splitlines()]
    text = "\n".join(lines)

    name = next((m.group(1).strip() for pattern in NAME_PATTERNS if (m := pattern.search(text))), "")
    if not name:
        review.append("full name not found: write it in full_name before using this file")
    email_match = EMAIL.search(text)
    email = email_match.group(0) if email_match else ""
    if not email:
        review.append("no email address found: add it under contact if you want it on the CV")

    facts: list[dict[str, str]] = []
    current_kind: str | None = None
    counters: dict[str, int] = {}
    for number, line in enumerate(lines, start=1):
        section = SECTION.search(line)
        if section:
            current_kind = _section_kind(section.group(1))
            if current_kind is None:
                review.append(f"line {number}: section '{section.group(1)}' has no known kind, its items are skipped")
            continue
        raw = _item_text(line)
        if raw is None:
            continue
        if current_kind is None:
            review.append(f"line {number}: item outside a known section, skipped: {line.strip()[:80]}")
            continue
        content = unwrap(raw)
        if not content:
            review.append(f"line {number}: empty item skipped")
            continue
        if "\\" in content:
            review.append(f"line {number}: unknown LaTeX command kept as written, check it: {content[:80]}")
        counters[current_kind] = counters.get(current_kind, 0) + 1
        facts.append({"id": f"{current_kind}-{counters[current_kind]}", "kind": current_kind, "text": content})

    if not facts:
        review.append("no facts found: the template may use a structure this converter does not read")
    for kind in ("summary",):
        if counters.get(kind, 0) == 0:
            review.append(f"no {kind} facts: add them by hand, the converter reads items only")

    master: dict[str, object] = {
        "draft": True,
        "full_name": name or "REVIEW: full name",
        "headline": "",
        "contact": {"email": email, "phone": "", "location": "", "links": []},
        "facts": facts,
    }
    return Conversion(master=master, review=review)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert a LaTeX CV into a draft master_cv.json.")
    parser.add_argument("tex", type=Path, help="the LaTeX source of your CV")
    parser.add_argument("--out", type=Path, required=True, help="where to write the draft JSON")
    parser.add_argument("--force", action="store_true", help="overwrite an existing file")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.out.exists() and not args.force:
        print(f"refusing to overwrite {args.out} (use --force)", file=sys.stderr)
        return 1
    conversion = convert(args.tex.read_text(encoding="utf-8"))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(conversion.master, ensure_ascii=False, indent=2), encoding="utf-8")
    review_path = args.out.with_suffix(".review.txt")
    review_path.write_text("\n".join(conversion.review) + "\n", encoding="utf-8")
    print(f"draft written to {args.out.resolve()}")
    print(f"review notes in {review_path.resolve()} ({len(conversion.review)} item(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
