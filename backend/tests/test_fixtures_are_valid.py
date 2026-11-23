"""Every literal CV fixture in the test suite is valid under the schema it claims to be.

The opt-in tests (for example the Tectonic compilations) are skipped on most machines, so a fixture they build can rot
without anyone noticing. This test reads every test file, finds each `Model.model_validate({...})` call whose argument is
a literal, and validates it. A fixture that the schema forbids fails here, in the default suite.
"""

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from linkedin_agent.cv.models import MasterCV, TailoredCV

TESTS = Path(__file__).resolve().parent
SAMPLE = TESTS.parent / "samples" / "sample_master_cv.json"
MODELS: dict[str, type] = {"MasterCV": MasterCV, "TailoredCV": TailoredCV}


def _inside_raises(tree: ast.AST) -> set[int]:
    """The ids of every node written inside a `with pytest.raises(...)` block."""
    inside: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        expects_error = any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Attribute)
            and item.context_expr.func.attr == "raises"
            for item in node.items
        )
        if expects_error:
            inside.update(id(child) for statement in node.body for child in ast.walk(statement))
    return inside


def _literal_fixtures() -> list[tuple[str, str, type, Any]]:
    found: list[tuple[str, str, type, Any]] = []
    for path in sorted(TESTS.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        expected_to_fail = _inside_raises(tree)
        for node in ast.walk(tree):
            if id(node) in expected_to_fail:
                continue  # a test that proves the schema refuses this value builds an invalid literal on purpose
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            target = node.func.value
            if node.func.attr != "model_validate" or not isinstance(target, ast.Name):
                continue
            model = MODELS.get(target.id)
            if model is None or not node.args:
                continue
            try:
                value = ast.literal_eval(node.args[0])
            except ValueError:
                continue  # not a literal (built at run time): nothing to check statically
            found.append((path.name, f"line {node.lineno}", model, value))
    return found


FIXTURES = _literal_fixtures()


def test_the_scan_finds_the_literal_fixtures() -> None:
    assert len(FIXTURES) >= 3  # the test suite builds CV fixtures from literals; an empty scan would be a broken check


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda item: f"{item[0]}:{item[1]}")
def test_a_literal_fixture_satisfies_its_schema(fixture: tuple[str, str, type, Any]) -> None:
    _, _, model, value = fixture
    model.model_validate(value)  # raises ValidationError with the offending field when it does not


def test_the_sample_master_cv_is_valid() -> None:
    MasterCV.model_validate(json.loads(SAMPLE.read_text(encoding="utf-8")))
