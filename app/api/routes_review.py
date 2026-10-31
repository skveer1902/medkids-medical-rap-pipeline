from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_settings_dep, requires
from app.api.presenters import rap_detail, rap_history
from app.core.config import Settings
from app.models.schemas import RapHistory, ReviewDecisionOut, ReviewRequest
from app.services.pipeline import get_rap
from app.services.review_service import approval_blockers, export_payload, review_rap
from app.services.security import Principal

router = APIRouter(prefix="/api/raps", tags=["human review"])


@router.post("/{rap_id}/review", summary="Reviewer decision: approve, reject, request_revision or comment")
def review(
    rap_id: int,
    body: ReviewRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    principal: Principal = Depends(requires("review")),
) -> dict[str, Any]:
    rap = get_rap(db, rap_id)
    decision = review_rap(db, rap, body, principal, settings)
    return {
        "decision": ReviewDecisionOut.model_validate(decision).model_dump(mode="json"),
        "rap": rap_detail(rap, settings).model_dump(mode="json"),
        "approval_blockers": approval_blockers(rap, settings) if rap.status == "pending_review" else [],
    }


@router.get("/{rap_id}/history", response_model=RapHistory,
            summary="All versions, validations, evaluations, reviews and status transitions")
def history(rap_id: int, db: Session = Depends(get_db), _: Principal = Depends(requires("read"))) -> RapHistory:
    return rap_history(get_rap(db, rap_id))


@router.get("/{rap_id}/export", summary="Structured export of an APPROVED rap (409 otherwise)")
def export(rap_id: int, db: Session = Depends(get_db), _: Principal = Depends(requires("read"))) -> dict[str, Any]:
    return export_payload(get_rap(db, rap_id))
