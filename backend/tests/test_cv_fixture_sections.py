"""Every CV section written in the test suite is a section the schema accepts.

The schema forbids some sections (the model cannot write a summary or an education line). A test that builds a CV with
a forbidden section fails at validation, and an opt-in test that does so stays green by being skipped. This module reads
every test file, finds each literal section a test gives to a CV, and checks it against the schema, so the mistake is
caught by the default run.

A test that checks the refusal on purpose marks the line with the comment "forbidden on purpose".
"""

import ast
from pathlib import Path
from typing import get_args

from linkedin_agent.cv.models import Section

TESTS_DIR = Path(__file__).resolve().parent
THIS_FILE = Path(__file__).resolve()
ALLOWED = set(get_args(Section))
DELIBERATE = "forbidden on purpose"
CV_HELPERS = {"tailored", "_cv"}  # helpers of the test modules that build a CV from (section, ...) arguments


def _literal_sections(tree: ast.AST, lines: list[str]) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):  # {"section": "..."}
            for key, value in zip(node.keys, node.values, strict=False):
                if isinstance(key, ast.Constant) and key.value == "section" and _is_text(value):
                    found.append((value.lineno, value.value))  # type: ignore[attr-defined]
        if isinstance(node, ast.keyword) and node.arg == "section" and _is_text(node.value):
            found.append((node.value.lineno, node.value.value))  # type: ignore[attr-defined]
        if isinstance(node, ast.Call) and _called_name(node) in CV_HELPERS:
            for argument in node.args:
                if isinstance(argument, ast.Tuple) and argument.elts and _is_text(argument.elts[0]):
                    found.append((argument.elts[0].lineno, argument.elts[0].value))  # type: ignore[attr-defined]
            if _called_name(node) == "_cv" and node.args and _is_text(node.args[0]):
                found.append((node.args[0].lineno, node.args[0].value))  # type: ignore[attr-defined]
    return [(line, section) for line, section in found if DELIBERATE not in lines[line - 1]]


def _is_text(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _called_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _scan() -> tuple[list[str], list[str]]:
    """(problems, every section found): a problem is a literal section the schema does not accept."""
    problems: list[str] = []
    everything: list[str] = []
    for path in sorted(TESTS_DIR.glob("*.py")):
        if path == THIS_FILE:
            continue
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        for line, section in _literal_sections(ast.parse(source, filename=str(path)), lines):
            everything.append(section)
            if section not in ALLOWED:
                problems.append(f"{path.name}:{line}: section {section!r} is not one of {sorted(ALLOWED)}")
    return problems, everything


def test_no_test_builds_a_cv_with_a_section_the_schema_forbids() -> None:
    problems, _ = _scan()
    assert problems == [], "\n".join(problems)


def test_the_scan_really_reads_the_cv_fixtures() -> None:
    # A scan that finds nothing would pass for the wrong reason: the suite has many CV sections.
    _, everything = _scan()
    assert len(everything) >= 20
    assert {"skill", "experience", "project"} <= set(everything)
