"""The environment template documents every setting, and names no setting that does not exist."""

import re
from pathlib import Path

from linkedin_agent.core.settings import Settings

TEMPLATE = Path(__file__).resolve().parents[1] / ".env.example"
DECLARATION = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=", re.MULTILINE)


def _documented() -> set[str]:
    return set(DECLARATION.findall(TEMPLATE.read_text(encoding="utf-8")))


def test_every_setting_is_documented_in_the_template() -> None:
    settings = {name.upper() for name in Settings.model_fields}
    assert sorted(settings - _documented()) == []


def test_every_variable_in_the_template_is_a_real_setting() -> None:
    settings = {name.upper() for name in Settings.model_fields}
    assert sorted(_documented() - settings) == []


def test_the_model_cache_and_the_embedding_warm_up_are_documented() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "FASTEMBED_CACHE_DIR=" in text
    assert "EMBEDDING_WARM_ON_STARTUP=" in text
    assert "EMBEDDING_MODEL=" in text
