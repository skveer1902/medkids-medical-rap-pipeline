"""Topic creation and lookup (Stage 1 + source content processing)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import InputRejectedError, NotFoundError
from app.models.database import LearningObjective, SourceDocument, Topic
from app.models.schemas import TopicCreate
from app.services.content_processor import process_source
from app.services.security import Principal
from app.utils.text_utils import slugify


def create_topic(session: Session, data: TopicCreate, principal: Principal, settings: Settings) -> Topic:
    processed = process_source(data.educational_content, max_chars=settings.max_source_chars)
    base = slugify(data.topic)
    slug, n = base, 2
    while session.scalar(select(Topic.id).where(Topic.slug == slug)) is not None:
        slug, n = f"{base}-{n}", n + 1
    if data.primary_objective and data.primary_objective not in data.learning_objectives:
        objectives = [data.primary_objective, *data.learning_objectives]
    else:
        objectives = list(data.learning_objectives)
    if len(objectives) > 12:
        raise InputRejectedError("at most 12 learning objectives are allowed")

    topic = Topic(
        slug=slug,
        title=data.topic,
        category=data.category,
        target_age_min=data.target_age_min,
        target_age_max=data.target_age_max,
        primary_objective=data.primary_objective or objectives[0],
        forbidden_claims=[fc.model_dump() for fc in data.forbidden_claims],
        required_vocabulary=data.required_vocabulary,
        content_flags=processed.flags,
        is_demo_data=data.is_demo_data,
        created_by=principal.name,
    )
    topic.sources.append(
        SourceDocument(
            source_key=f"{slug}_src_001",
            title=data.source_title or f"{data.topic} educational content",
            uri=data.source_uri,
            version=data.source_version,
            content=processed.content,
            content_sha256=processed.sha256,
            is_approved_source=data.source_is_approved,
        )
    )
    for i, text in enumerate(objectives, start=1):
        topic.objectives.append(LearningObjective(objective_key=f"{slug}_obj_{i:02d}", text=text, position=i))
    session.add(topic)
    session.flush()
    return topic


def get_topic(session: Session, topic_id: int) -> Topic:
    topic = session.get(Topic, topic_id)
    if topic is None:
        raise NotFoundError(f"topic {topic_id} not found")
    return topic


def list_topics(session: Session, limit: int = 100, offset: int = 0) -> list[Topic]:
    return list(session.scalars(select(Topic).order_by(Topic.id).limit(limit).offset(offset)))
