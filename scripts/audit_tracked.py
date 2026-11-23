"""Secret and personal-data audit of the files that would be committed.

It scans the tracked files and the untracked files that git would not ignore. It reports the file, the line number and
the kind of finding. It never prints the matched value: a finding is a fact to look at, not a value to copy around.

Run from the repository root:

    python scripts/audit_tracked.py            # every file git would commit (tracked and not ignored)
    python scripts/audit_tracked.py --staged   # only what is staged (used by the pre-commit hook)

Exit status 0 when nothing is found, 1 otherwise.
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Callable
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Files that are binary, generated from code, or lock files whose hashes look like secrets without being one.
# The audit itself and its tests contain the patterns they look for, on purpose.
SKIPPED_NAMES = frozenset(
    {"uv.lock", "package-lock.json", "openapi.json", "schema.gen.ts", "audit_tracked.py", "test_audit_rules.py"}
)
SKIPPED_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".pyc", ".woff", ".woff2", ".svg")

# Domains reserved for documentation and testing (RFC 2606 and 6761): an address there identifies nobody.
RESERVED_TLDS = ("test", "example", "invalid", "localhost")
EXAMPLE_DOMAINS = ("example.com", "example.org", "example.net")
# Mail providers shared by millions of people: the domain identifies no one, only the local part could.
PROVIDER_DOMAINS = (
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com", "icloud.com", "proton.me",
    "gmx.com", "gmx.fr", "orange.fr", "free.fr", "laposte.net",
)
# Addresses that appear in tests as negative examples (an automatic sender that must be ignored): nobody is behind them.
PLACEHOLDER_ADDRESSES = frozenset(
    {
        "support@sentry.io",
        "jane.doe@yahoo.fr",
        "jane.doe@yahoo.co.uk",
        "jane.doe@gmx.de",
    }
)


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    allowed: Callable[[str], bool] | None = None


# A "name@x.webp" is an asset reference in a page or a test, not an address.
ASSET_SUFFIXES = ("png", "jpg", "jpeg", "gif", "webp", "svg", "css", "js", "ts", "py", "json", "md")


def _example_address(match: str) -> bool:
    domain = match.rsplit("@", 1)[-1].lower()
    last = domain.rsplit(".", 1)[-1]
    if last in RESERVED_TLDS or last in ASSET_SUFFIXES or match.lower() in PLACEHOLDER_ADDRESSES:
        return True
    if any(domain == d for d in PROVIDER_DOMAINS):
        return True
    return any(domain == d or domain.endswith("." + d) for d in EXAMPLE_DOMAINS)


def _digest(line: str, match: re.Match[str]) -> bool:
    """A hash written as an image digest (sha256:...) or a published checksum (a line that names SHA256) is not a secret."""
    before = line[max(0, match.start() - 8) : match.start()].lower()
    return before.endswith("sha256:") or "sha256" in line.lower()


RULES: tuple[Rule, ...] = (
    Rule("groq-key", re.compile(r"gsk_[A-Za-z0-9]{20,}")),
    Rule("openai-style-key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}")),
    Rule("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    Rule("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    Rule("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}")),
    Rule("private-key-block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    Rule(
        "personal-email",
        re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
        allowed=_example_address,
    ),
    Rule("absolute-windows-path", re.compile(r"\b[A-Za-z]:\\(?:Users|dev|Documents|Desktop)\\", re.IGNORECASE)),
    Rule("absolute-unix-home", re.compile(r"/(?:home|Users)/[a-z][a-z0-9_-]+/")),
    Rule("long-hex-string", re.compile(r"\b[0-9a-fA-F]{40,}\b")),
    Rule(
        "long-base64-string",
        re.compile(r"(?<![A-Za-z0-9+/=_-])(?=[A-Za-z0-9+/]*[0-9])(?=[A-Za-z0-9+/]*[A-Z])[A-Za-z0-9+/]{48,}={0,2}(?![A-Za-z0-9+/=_-])"),
    ),
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str


def scan_text(path: str, text: str) -> list[Finding]:
    """Findings in one file's text. Only the rule name and the position are returned, never the value."""
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for rule in RULES:
            for match in rule.pattern.finditer(line):
                if rule.allowed is not None and rule.allowed(match.group(0)):
                    continue
                if rule.name == "long-hex-string" and _digest(line, match):
                    continue
                findings.append(Finding(path, number, rule.name))
    return findings


def _is_skipped(path: str) -> bool:
    name = Path(path).name
    return name in SKIPPED_NAMES or path.lower().endswith(SKIPPED_SUFFIXES)


def candidate_files(staged_only: bool) -> list[str]:
    if staged_only:
        command = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"]
    else:
        command = ["git", "ls-files", "--cached", "--others", "--exclude-standard"]
    output = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return sorted({line.strip() for line in output.splitlines() if line.strip()})


def scan_repository(staged_only: bool = False) -> list[Finding]:
    findings: list[Finding] = []
    for relative in candidate_files(staged_only):
        if _is_skipped(relative):
            continue
        path = ROOT / relative
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        findings.extend(scan_text(relative, text))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--staged", action="store_true", help="scan only the staged files")
    args = parser.parse_args(argv)
    findings = scan_repository(staged_only=args.staged)
    for finding in findings:
        print(f"{finding.path}:{finding.line}: {finding.rule}")
    if findings:
        print(f"{len(findings)} finding(s). Remove the value, or move it to an ignored file, then run again.")
        return 1
    print("audit: no finding")
    return 0


if __name__ == "__main__":
    sys.exit(main())
