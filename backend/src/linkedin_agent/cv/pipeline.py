"""Tailor a master CV to one job: model proposes, grounding check decides what a human must look at."""

import json
from dataclasses import dataclass
from pathlib import Path

from ..core.exceptions import CVStructureError, LLMOutputError, ValidationFailedError
from ..infra.llm import complete_structured
from ..prompts import render
from .grounding import verify_grounding
from .models import MasterCV, TailoredCV

JOB_TEXT_MAX_CHARS = 3000


@dataclass(frozen=True)
class TailoringResult:
    cv: TailoredCV
    problems: list[str]

    @property
    def grounded(self) -> bool:
        return not self.problems


async def tailor_cv(job_title: str, job_text: str, master: MasterCV, feedback: str = "(none)") -> TailoringResult:
    facts = "\n".join(f"[{fact.id}] ({fact.kind}) {fact.text}" for fact in master.facts)
    prompt = render(
        "cv_tailor.v2",
        job_title=job_title,
        job_text=job_text[:JOB_TEXT_MAX_CHARS],
        facts=facts,
        feedback=feedback,
    )
    try:
        cv = await complete_structured(prompt, TailoredCV, retries=1)
    except LLMOutputError as exc:
        # The user sees this message in the run status. The validation details are in the log, not here.
        raise CVStructureError() from exc
    return TailoringResult(cv=cv, problems=verify_grounding(cv, master))


def load_master_cv(path: Path) -> MasterCV:
    """Read and validate a master CV JSON file.

    A file marked "draft": true (produced by the LaTeX importer) is refused until a human has reviewed it
    and removed the marker. Validation errors name the field that is wrong.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValidationFailedError("master CV must be a JSON object")
    if raw.get("draft") is True:
        raise ValidationFailedError(f'{path.name} is a draft: review every fact, then remove the "draft" key')
    raw.pop("draft", None)
    return MasterCV.model_validate(raw)
