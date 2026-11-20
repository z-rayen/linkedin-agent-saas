from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr


class UserCreate(BaseModel):
    email: EmailStr
    password: str


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


class ProfileConfigOut(ProfileConfigIn):
    model_config = ConfigDict(from_attributes=True)
