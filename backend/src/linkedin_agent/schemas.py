import json
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from .domain.accounts import normalise_address


class UserCreate(BaseModel):
    email: EmailStr
    password: str

    @field_validator("email", mode="before")
    @classmethod
    def normalise_email(cls, value: object) -> object:
        """Case and surrounding spaces do not make another address: " Bob@Example.com " is "bob@example.com"."""
        return normalise_address(value) if isinstance(value, str) else value


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ContentCreate(BaseModel):
    title: str
    context: str
    source: str = "manual"


class ContentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    post_text: str
    status: str
    source: str
    created_at: datetime


class ProfileConfigIn(BaseModel):
    field: str
    skills: str
    level: str
    min_match_score: int = 70
    job_feed_url: str | None = None
    cert_feed_url: str | None = None
    # Watch preferences. Omitted (null) means "keep what is stored"; an empty list or string clears it.
    regions: list[str] | None = Field(default=None, max_length=10)
    languages: list[str] | None = Field(default=None, max_length=10)
    offer_type: str | None = Field(default=None, max_length=120)


class ProfileConfigOut(ProfileConfigIn):
    model_config = ConfigDict(from_attributes=True)

    @field_validator("regions", "languages", mode="before")
    @classmethod
    def _stored_list(cls, value: object) -> object:
        """The column holds a JSON list as text; a missing or unreadable value reads as no list."""
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except ValueError:
                return None
            return parsed if isinstance(parsed, list) else None
        return value
