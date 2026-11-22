"""Structured LLM outputs. Each prompt that expects JSON has one model here.

Parsing and validation happen in one place (infra.llm.complete_structured), so
callers receive typed values or an explicit error, never a half-parsed dict.
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

MAX_PLANNER_QUERIES = 3
MAX_SCORE = 100
UNKNOWN_INTENT_FALLBACK = "search"


class ScoreOutput(BaseModel):
    """Relevance of one watch item for the user's profile."""

    score: int = Field(ge=0, le=MAX_SCORE)
    justification: str = ""


class PlannerOutput(BaseModel):
    """Research agent plan: which web searches to run, and whether to draft a post."""

    intent: Literal["search", "search_and_post"] = "search"
    queries: list[str] = Field(min_length=1, max_length=MAX_PLANNER_QUERIES)

    @field_validator("intent", mode="before")
    @classmethod
    def unknown_intent_becomes_search(cls, value: object) -> object:
        """Anything other than the two known intents is treated as a plain search."""
        if value in ("search", "search_and_post"):
            return value
        return UNKNOWN_INTENT_FALLBACK


class ReflectOutput(BaseModel):
    """Research agent reflection: is the evidence enough, and if not, which queries would fill the gaps."""

    sufficient: bool = True
    queries: list[str] = Field(default_factory=list, max_length=MAX_PLANNER_QUERIES)


class ApplicationEmailOutput(BaseModel):
    """Subject and body of a job application email."""

    subject: str
    body: str


class JobAnalysis(BaseModel):
    """What the job offer asks for, as read by the model. Every field is bounded: it is data, not a decision."""

    language: str = Field(default="unknown", max_length=20)
    requirements: list[str] = Field(default_factory=list, max_length=20)
    company: str | None = Field(default=None, max_length=200)
