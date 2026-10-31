from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_pipeline, get_settings_dep, requires
from app.core.config import Settings
from app.models.schemas import (
    FactApprovalRequest,
    FactApprovalResponse,
    FactExtractionResponse,
    FactOut,
    TopicCreate,
    TopicOut,
    TopicSummary,
)
from app.services.fact_extractor import FactExtractor, approve_facts
from app.services.pipeline import Pipeline
from app.services.security import Principal
from app.services.topic_service import create_topic, get_topic, list_topics

router = APIRouter(prefix="/api/topics", tags=["topics"])


@router.post("", response_model=TopicOut, status_code=status.HTTP_201_CREATED,
             summary="Submit an educational topic with source content and learning objectives")
def post_topic(
    body: TopicCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    principal: Principal = Depends(requires("author")),
) -> TopicOut:
    topic = create_topic(db, body, principal, settings)
    db.commit()
    db.refresh(topic)
    return TopicOut.model_validate(topic)


@router.get("", response_model=list[TopicSummary])
def get_topics(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: Principal = Depends(requires("read")),
) -> list[TopicSummary]:
    return [TopicSummary.model_validate(t) for t in list_topics(db, limit, offset)]


@router.get("/{topic_id}", response_model=TopicOut)
def get_topic_by_id(topic_id: int, db: Session = Depends(get_db), _: Principal = Depends(requires("read"))) -> TopicOut:
    return TopicOut.model_validate(get_topic(db, topic_id))


@router.post("/{topic_id}/facts/extract", response_model=FactExtractionResponse,
             summary="Propose candidate facts (never auto-approved)")
def extract_facts(
    topic_id: int,
    db: Session = Depends(get_db),
    pipeline: Pipeline = Depends(get_pipeline),
    _: Principal = Depends(requires("author")),
) -> FactExtractionResponse:
    topic = get_topic(db, topic_id)
    extractor = FactExtractor(pipeline.provider, json_max_attempts=pipeline.settings.llm_json_max_attempts)
    run_id, created = extractor.extract(db, topic)
    pipeline._persist_llm_calls(db, run_id)
    db.commit()
    return FactExtractionResponse(
        topic_id=topic.id,
        run_id=run_id,
        candidates=[FactOut.model_validate(f) for f in created if f.status == "candidate"],
        uncertain=[FactOut.model_validate(f) for f in created if f.status == "uncertain"],
    )


@router.post("/{topic_id}/facts/approve", response_model=FactApprovalResponse,
             summary="Medical reviewer approves/rejects candidate facts or adds verified manual facts")
def approve_topic_facts(
    topic_id: int,
    body: FactApprovalRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(requires("approve_facts")),
) -> FactApprovalResponse:
    topic = get_topic(db, topic_id)
    result = approve_facts(db, topic, body, principal)
    db.commit()
    return FactApprovalResponse(
        topic_id=topic.id, approved=result["approved"], rejected=result["rejected"], added=result["added"],
        approved_fact_count=sum(f.status == "approved" for f in topic.facts),
    )
