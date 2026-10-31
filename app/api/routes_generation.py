from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_pipeline, get_settings_dep, requires
from app.api.presenters import rap_detail
from app.core.config import Settings
from app.models.database import Rap
from app.models.schemas import GenerateResponse, RapDetail, RapGenerateRequest, RapStatus, RapSummary
from app.services.pipeline import Pipeline, get_rap
from app.services.security import Principal

router = APIRouter(prefix="/api/raps", tags=["raps"])


@router.post("/generate", response_model=GenerateResponse, status_code=status.HTTP_201_CREATED,
             summary="Generate rap candidate(s) from approved facts; optionally run checks + capped auto-revision")
def generate(
    body: RapGenerateRequest,
    db: Session = Depends(get_db),
    pipeline: Pipeline = Depends(get_pipeline),
    settings: Settings = Depends(get_settings_dep),
    principal: Principal = Depends(requires("author")),
) -> GenerateResponse:
    raps = pipeline.generate(db, body, principal)
    return GenerateResponse(raps=[rap_detail(r, settings) for r in raps])


@router.get("", response_model=list[RapSummary])
def list_raps(
    topic_id: int | None = None,
    status_filter: RapStatus | None = Query(default=None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: Principal = Depends(requires("read")),
) -> list[RapSummary]:
    q = select(Rap).order_by(Rap.id.desc())
    if topic_id is not None:
        q = q.where(Rap.topic_id == topic_id)
    if status_filter is not None:
        q = q.where(Rap.status == status_filter)
    return [RapSummary.model_validate(r) for r in db.scalars(q.limit(limit).offset(offset))]


@router.get("/{rap_id}", response_model=RapDetail, summary="Lyrics, validation findings, scores and reviews")
def get_rap_detail(
    rap_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    _: Principal = Depends(requires("read")),
) -> RapDetail:
    return rap_detail(get_rap(db, rap_id), settings)
