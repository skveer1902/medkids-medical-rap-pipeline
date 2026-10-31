"""SQLAlchemy ORM models and session management.

Schema initialisation: :func:`init_db` calls ``Base.metadata.create_all`` and
records ``SCHEMA_VERSION`` in the ``schema_meta`` table. Run it with
``python scripts/init_db.py`` (the API also calls it on startup). For
production, replace this with Alembic migrations (see ARCHITECTURE.md).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)
from sqlalchemy.pool import StaticPool

SCHEMA_VERSION = "1"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class SchemaMeta(Base):
    __tablename__ = "schema_meta"
    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(String(200))


class Topic(TimestampMixin, Base):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(60))
    target_age_min: Mapped[int] = mapped_column(Integer)
    target_age_max: Mapped[int] = mapped_column(Integer)
    primary_objective: Mapped[str | None] = mapped_column(Text, nullable=True)
    forbidden_claims: Mapped[list[Any]] = mapped_column(JSON, default=list)
    required_vocabulary: Mapped[list[Any]] = mapped_column(JSON, default=list)
    content_flags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    is_demo_data: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(String(80))

    sources: Mapped[list[SourceDocument]] = relationship(
        back_populates="topic", cascade="all, delete-orphan", order_by="SourceDocument.id"
    )
    objectives: Mapped[list[LearningObjective]] = relationship(
        back_populates="topic", cascade="all, delete-orphan", order_by="LearningObjective.position"
    )
    facts: Mapped[list[Fact]] = relationship(
        back_populates="topic", cascade="all, delete-orphan", order_by="Fact.id"
    )


class SourceDocument(Base):
    __tablename__ = "source_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"), index=True)
    source_key: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(200))
    uri: Mapped[str | None] = mapped_column(String(500), nullable=True)
    version: Mapped[str] = mapped_column(String(40), default="1")
    content: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    is_approved_source: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    topic: Mapped[Topic] = relationship(back_populates="sources")
    __table_args__ = (UniqueConstraint("topic_id", "source_key", name="uq_source_key"),)


class LearningObjective(Base):
    __tablename__ = "learning_objectives"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"), index=True)
    objective_key: Mapped[str] = mapped_column(String(80))
    text: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, default=0)

    topic: Mapped[Topic] = relationship(back_populates="objectives")


class Fact(TimestampMixin, Base):
    """A medical fact. Only ``status == 'approved'`` facts are used for generation."""

    __tablename__ = "facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"), index=True)
    fact_key: Mapped[str] = mapped_column(String(80), index=True)
    text: Mapped[str] = mapped_column(Text)
    source_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_documents.id"), nullable=True
    )
    evidence_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    # candidate | uncertain | approved | rejected
    status: Mapped[str] = mapped_column(String(20), default="candidate", index=True)
    uncertainty_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposed_by: Mapped[str] = mapped_column(String(120))
    extraction_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_demo_data: Mapped[bool] = mapped_column(Boolean, default=False)

    topic: Mapped[Topic] = relationship(back_populates="facts")
    __table_args__ = (UniqueConstraint("topic_id", "fact_key", name="uq_fact_key"),)


class Rap(TimestampMixin, Base):
    __tablename__ = "raps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"), index=True)
    # draft | validation_failed | pending_review | revision_requested | approved | rejected
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    age_band: Mapped[str] = mapped_column(String(10))
    style: Mapped[str] = mapped_column(String(40))
    generation_request: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    auto_revision_attempts: Mapped[int] = mapped_column(Integer, default=0)
    needs_human_intervention: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(String(80))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    topic: Mapped[Topic] = relationship()
    versions: Mapped[list[RapVersion]] = relationship(
        back_populates="rap", cascade="all, delete-orphan", order_by="RapVersion.version_number"
    )
    reviews: Mapped[list[ReviewDecision]] = relationship(
        back_populates="rap", cascade="all, delete-orphan", order_by="ReviewDecision.id"
    )
    events: Mapped[list[WorkflowEvent]] = relationship(
        back_populates="rap", cascade="all, delete-orphan", order_by="WorkflowEvent.id"
    )


class RapVersion(Base):
    """An immutable snapshot of lyrics plus the exact generation provenance."""

    __tablename__ = "rap_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rap_id: Mapped[int] = mapped_column(ForeignKey("raps.id", ondelete="CASCADE"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    lyrics: Mapped[dict[str, Any]] = mapped_column(JSON)
    objectives_covered: Mapped[list[Any]] = mapped_column(JSON, default=list)
    change_type: Mapped[str] = mapped_column(String(30))  # generated | auto_revision | reviewer_revision
    changed_line_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(120))
    prompt_name: Mapped[str] = mapped_column(String(60))
    prompt_version: Mapped[str] = mapped_column(String(20))
    prompt_sha256: Mapped[str] = mapped_column(String(64))
    generation_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    approved_fact_keys: Mapped[list[Any]] = mapped_column(JSON, default=list)
    run_id: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    rap: Mapped[Rap] = relationship(back_populates="versions")
    validations: Mapped[list[ValidationResult]] = relationship(
        back_populates="rap_version", cascade="all, delete-orphan", order_by="ValidationResult.id"
    )
    evaluations: Mapped[list[Evaluation]] = relationship(
        back_populates="rap_version", cascade="all, delete-orphan", order_by="Evaluation.id"
    )
    __table_args__ = (UniqueConstraint("rap_id", "version_number", name="uq_rap_version"),)


class ValidationResult(Base):
    __tablename__ = "validation_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rap_version_id: Mapped[int] = mapped_column(
        ForeignKey("rap_versions.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))  # medical | safety
    status: Mapped[str] = mapped_column(String(30))  # passed | requires_review | failed
    report: Mapped[dict[str, Any]] = mapped_column(JSON)
    validator_version: Mapped[str] = mapped_column(String(20))
    run_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    rap_version: Mapped[RapVersion] = relationship(back_populates="validations")


class Evaluation(Base):
    __tablename__ = "evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rap_version_id: Mapped[int] = mapped_column(
        ForeignKey("rap_versions.id", ondelete="CASCADE"), index=True
    )
    total_score: Mapped[float] = mapped_column(Float)
    scores: Mapped[dict[str, Any]] = mapped_column(JSON)
    hard_gates: Mapped[dict[str, Any]] = mapped_column(JSON)
    automated_checks_passed: Mapped[bool] = mapped_column(Boolean)
    recommendations: Mapped[list[Any]] = mapped_column(JSON, default=list)
    rubric_version: Mapped[str] = mapped_column(String(20))
    weights: Mapped[dict[str, Any]] = mapped_column(JSON)
    threshold: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    rap_version: Mapped[RapVersion] = relationship(back_populates="evaluations")


class ReviewDecision(Base):
    __tablename__ = "review_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rap_id: Mapped[int] = mapped_column(ForeignKey("raps.id", ondelete="CASCADE"), index=True)
    rap_version_number: Mapped[int] = mapped_column(Integer)
    reviewer_name: Mapped[str] = mapped_column(String(80))
    reviewer_role: Mapped[str] = mapped_column(String(30))
    decision: Mapped[str] = mapped_column(String(30))  # approve | reject | request_revision | comment
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    issues: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    rap: Mapped[Rap] = relationship(back_populates="reviews")


class WorkflowEvent(Base):
    """Audit trail of every status transition."""

    __tablename__ = "workflow_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rap_id: Mapped[int] = mapped_column(ForeignKey("raps.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    to_status: Mapped[str] = mapped_column(String(30))
    actor: Mapped[str] = mapped_column(String(80))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    version_number: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    rap: Mapped[Rap] = relationship(back_populates="events")


class LLMCallLog(Base):
    """Token usage / cost / latency per LLM call. Never stores prompt or output text."""

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    task: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(120))
    prompt_name: Mapped[str | None] = mapped_column(String(60), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --- Engine / session -----------------------------------------------------


def make_engine(database_url: str) -> Engine:
    kwargs: dict[str, Any] = {}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if database_url in {"sqlite://", "sqlite:///:memory:"}:
            kwargs["poolclass"] = StaticPool
    engine = create_engine(database_url, **kwargs)

    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # type: ignore[no-untyped-def]
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def init_db(engine: Engine) -> None:
    """Create all tables (idempotent) and record the schema version."""
    if engine.url.get_backend_name() == "sqlite" and engine.url.database:
        import os

        db_dir = os.path.dirname(os.path.abspath(engine.url.database))
        os.makedirs(db_dir, exist_ok=True)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        meta = session.get(SchemaMeta, "schema_version")
        if meta is None:
            session.add(SchemaMeta(key="schema_version", value=SCHEMA_VERSION))
        elif meta.value != SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema version {meta.value} != code version {SCHEMA_VERSION}; "
                "run a migration before starting the app."
            )
        session.commit()


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
    finally:
        session.close()


__all__ = [
    "Base",
    "Topic",
    "SourceDocument",
    "LearningObjective",
    "Fact",
    "Rap",
    "RapVersion",
    "ValidationResult",
    "Evaluation",
    "ReviewDecision",
    "WorkflowEvent",
    "LLMCallLog",
    "make_engine",
    "init_db",
    "make_session_factory",
    "select",
]
