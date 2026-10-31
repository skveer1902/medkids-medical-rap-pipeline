from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_pipeline, get_settings_dep, requires
from app.api.presenters import rap_detail
from app.core.config import Settings
from app.models.schemas import ReviseRequest
from app.services.pipeline import Pipeline, get_rap
from app.services.security import Principal

router = APIRouter(prefix="/api/raps", tags=["validation & evaluation"])


@router.post("/{rap_id}/validate", summary="Run medical-accuracy and child-safety validation on the current version")
def validate(
    rap_id: int,
    db: Session = Depends(get_db),
    pipeline: Pipeline = Depends(get_pipeline),
    settings: Settings = Depends(get_settings_dep),
    _: Principal = Depends(requires("author")),
) -> dict[str, Any]:
    rap = get_rap(db, rap_id)
    medical, safety = pipeline.validate(db, rap)
    return {"medical_accuracy": medical.model_dump(), "child_safety": safety.model_dump(),
            "rap": rap_detail(rap, settings).model_dump(mode="json")}


@router.post("/{rap_id}/evaluate", summary="Score the current version (requires validation first)")
def evaluate(
    rap_id: int,
    db: Session = Depends(get_db),
    pipeline: Pipeline = Depends(get_pipeline),
    settings: Settings = Depends(get_settings_dep),
    _: Principal = Depends(requires("author")),
) -> dict[str, Any]:
    rap = get_rap(db, rap_id)
    report = pipeline.evaluate(db, rap)
    return {"evaluation": report.model_dump(), "rap": rap_detail(rap, settings).model_dump(mode="json")}


@router.post("/{rap_id}/revise", summary="Targeted revision of failing / reviewer-flagged lines only")
def revise(
    rap_id: int,
    body: ReviseRequest = Body(default_factory=ReviseRequest),
    db: Session = Depends(get_db),
    pipeline: Pipeline = Depends(get_pipeline),
    settings: Settings = Depends(get_settings_dep),
    principal: Principal = Depends(requires("author")),
) -> dict[str, Any]:
    rap = get_rap(db, rap_id)
    changed = pipeline.revise(db, rap, principal, auto_validate=body.auto_validate)
    return {"changed": changed, "rap": rap_detail(rap, settings).model_dump(mode="json")}
