"""Data model of the CV pipeline.

MasterCV is the human-written source of truth. Every fact in it has an id. A tailored CV
never writes free text about the candidate: every line cites the fact ids it is based on,
and grounding.py checks those citations.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FactKind = Literal["experience", "project", "education", "skill", "summary"]
Section = Literal["summary", "skill", "experience", "project"]


class Fact(BaseModel):
    """One verifiable statement from the master CV or the knowledge base."""

    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_\-]+$")
    kind: FactKind
    text: str = Field(min_length=1)


class Contact(BaseModel):
    email: str = ""
    phone: str = ""
    location: str = ""
    links: list[str] = Field(default_factory=list)


class MasterCV(BaseModel):
    """The candidate's complete CV, written once by hand (or from an anonymized sample).

    Unknown keys are rejected: a draft marker or a stray field cannot slip through silently.
    """

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=1)
    headline: str = ""
    contact: Contact = Field(default_factory=Contact)
    facts: list[Fact] = Field(min_length=1)

    def fact_ids(self) -> set[str]:
        return {fact.id for fact in self.facts}

    def facts_by_id(self) -> dict[str, Fact]:
        return {fact.id: fact for fact in self.facts}


class TailoredLine(BaseModel):
    """One line of the tailored CV, with the facts it is based on."""

    section: Section
    text: str = Field(min_length=1)
    source_ids: list[str] = Field(min_length=1)


class TailoredCV(BaseModel):
    lines: list[TailoredLine] = Field(min_length=1)
