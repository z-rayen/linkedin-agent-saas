from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, DateTime, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import relationship

from .clock import utcnow
from .database import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    created_at = Column(DateTime, default=utcnow)

    oauth_tokens = relationship("OAuthToken", back_populates="user")
    profile = relationship("ProfileConfig", back_populates="user", uselist=False)
    content_items = relationship("ContentQueue", back_populates="user")


class OAuthToken(Base):
    __tablename__ = "oauth_tokens"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    provider = Column(String, nullable=False)  # 'linkedin', 'github', 'kaggle'
    access_token = Column(Text, nullable=False)  # à chiffrer avant stockage en prod
    refresh_token = Column(Text, nullable=True)
    expires_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="oauth_tokens")


class ProfileConfig(Base):
    __tablename__ = "profile_config"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True)
    field = Column(String)  # ex: "Machine Learning, Data Science"
    skills = Column(Text)  # liste séparée par virgules
    level = Column(String)  # "Student", "Junior", "Senior"
    min_match_score = Column(Integer, default=70)
    job_feed_url = Column(String)
    cert_feed_url = Column(String)
    master_cv_json = Column(Text, nullable=True)  # validated MasterCV, written by the user

    user = relationship("User", back_populates="profile")


class ContentQueue(Base):
    __tablename__ = "content_queue"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    source = Column(String)  # 'github', 'kaggle', 'manual'
    source_ref = Column(String)
    title = Column(String)
    post_text = Column(Text)
    status = Column(String, default="draft")  # draft, approved, published, error
    post_url = Column(String, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    published_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="content_items")


class WatchResult(Base):
    __tablename__ = "watch_results"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    type = Column(String)  # 'job' ou 'cert'
    title = Column(String)
    link = Column(String)
    match_score = Column(Integer, nullable=True)
    justification = Column(Text, nullable=True)
    cover_letter = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class AgentMessage(Base):
    __tablename__ = "agent_messages"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    role = Column(String)  # 'user' ou 'assistant'
    content = Column(Text)
    created_at = Column(DateTime, default=utcnow)


class Application(Base):
    """One job application: the prepared email, its tailored CV, and where it is in its lifecycle."""

    __tablename__ = "applications"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    watch_result_id = Column(Integer, ForeignKey("watch_results.id"), nullable=True)
    company = Column(String, nullable=True)
    job_title = Column(String, nullable=False)
    recipient_email = Column(String, nullable=False, index=True)
    subject = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    language = Column(String, nullable=False, default="en")
    cv_pdf_path = Column(String, nullable=True)
    status = Column(String, nullable=False, default="prepared", index=True)
    smtp_message_id = Column(String, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    approved_at = Column(DateTime, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    # Same-company key (see domain.applications.company_key), used by the send policy.
    company_key = Column(String, nullable=False, default="", index=True)
    tailored_cv_json = Column(Text, nullable=True)  # the tailored CV lines, each with its source ids
    grounding_problems = Column(Text, nullable=False, default="[]")  # JSON list; approval is refused when non-empty
    # Hash of the approved content (email and CV lines). Set at approval, checked at send time.
    approved_content_hash = Column(String, nullable=True)
    # The PDF frozen at approval: exactly these bytes are attached at send. Its sha256 is part of the approval hash.
    cv_pdf = Column(LargeBinary, nullable=True)
    cv_pdf_sha256 = Column(String, nullable=True)
    # Set when the claim for sending is taken. A "sending" row older than the timeout needs verification.
    sending_started_at = Column(DateTime, nullable=True)


class ProfileKnowledge(Base):
    __tablename__ = "profile_knowledge"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    content = Column(Text)
    source = Column(String, default="manual")
    embedding = Column(Vector(384))
    created_at = Column(DateTime, default=utcnow)


class PreparationRun(Base):
    """One background run of the application graph. Its status is always visible and never left dangling."""

    __tablename__ = "preparation_runs"
    id = Column(Integer, primary_key=True, index=True)  # internal: checkpoint thread id, never shown to clients
    # The only identifier clients see. Random and unguessable, so a sequential id cannot be walked.
    public_id = Column(String, nullable=False, unique=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    lock_key = Column(String, nullable=False)  # same user, recipient and job title: one active run at a time
    status = Column(
        String, nullable=False, default="queued", index=True
    )  # queued|running|awaiting_review|closed|failed
    step = Column(String, nullable=True)  # the graph node currently running
    application_id = Column(Integer, ForeignKey("applications.id"), nullable=True)
    payload_json = Column(Text, nullable=False)  # the request (job text, recipient, company): needed to start
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    heartbeat_at = Column(DateTime, default=utcnow)
    # What the run does: "application" (preparing an application) or "research" (the research agent). A run of one
    # kind is never read, resumed or reported as the other.
    kind = Column(String, nullable=False, default="application", server_default="application", index=True)
    result_json = Column(Text, nullable=True)  # the research answer, once the run is done; empty for applications
