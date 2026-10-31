"""Convert ORM objects into API response models."""

from __future__ import annotations

from app.core.config import Settings
from app.models.database import Rap
from app.models.schemas import (
    RapDetail,
    RapHistory,
    RapVersionOut,
    ReviewDecisionOut,
    WorkflowEventOut,
)
from app.services.pipeline import current_version, latest_evaluation, latest_validation
from app.services.review_service import approvals_for_current_version


def _evaluation_dict(ev) -> dict | None:  # type: ignore[no-untyped-def]
    if ev is None:
        return None
    return {
        "total_score": ev.total_score, "scores": ev.scores, "hard_gates": ev.hard_gates,
        "automated_checks_passed": ev.automated_checks_passed, "recommendations": ev.recommendations,
        "rubric_version": ev.rubric_version, "weights": ev.weights, "threshold": ev.threshold,
        "created_at": ev.created_at.isoformat(),
    }


def rap_detail(rap: Rap, settings: Settings) -> RapDetail:
    v = current_version(rap)
    med, saf = latest_validation(v, "medical"), latest_validation(v, "safety")
    return RapDetail(
        id=rap.id, topic_id=rap.topic_id, status=rap.status, age_band=rap.age_band, style=rap.style,  # type: ignore[arg-type]
        current_version=rap.current_version, needs_human_intervention=rap.needs_human_intervention,
        created_at=rap.created_at, updated_at=rap.updated_at, title=v.title, lyrics=v.lyrics,
        objectives_covered=list(v.objectives_covered or []),
        medical_validation=med.report if med else None,
        safety_validation=saf.report if saf else None,
        evaluation=_evaluation_dict(latest_evaluation(v)),
        reviews=[ReviewDecisionOut.model_validate(r) for r in rap.reviews],
        approvals_for_current_version=sorted(approvals_for_current_version(rap)),
        approvals_required=list(settings.required_approval_roles),
        auto_revision_attempts=rap.auto_revision_attempts,
        is_demo_data=rap.topic.is_demo_data,
    )


def rap_history(rap: Rap) -> RapHistory:
    validations, evaluations = [], []
    for v in rap.versions:
        for val in v.validations:
            validations.append({"version": v.version_number, "kind": val.kind, "status": val.status,
                                "validator_version": val.validator_version, "created_at": val.created_at.isoformat(),
                                "report": val.report})
        for ev in v.evaluations:
            evaluations.append({"version": v.version_number, **(_evaluation_dict(ev) or {})})
    return RapHistory(
        rap_id=rap.id, status=rap.status,  # type: ignore[arg-type]
        versions=[RapVersionOut.model_validate(v) for v in rap.versions],
        events=[WorkflowEventOut.model_validate(e) for e in rap.events],
        reviews=[ReviewDecisionOut.model_validate(r) for r in rap.reviews],
        validations=validations, evaluations=evaluations,
    )
