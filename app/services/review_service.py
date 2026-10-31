"""Stage 9/10 - human review, approval gate and approved-lyrics storage."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import PermissionDeniedError, WorkflowError
from app.core.logging import get_logger, log_event
from app.models.database import Rap, ReviewDecision
from app.models.schemas import ReviewRequest
from app.services.pipeline import current_version, latest_evaluation, latest_validation
from app.services.security import PERMISSIONS, Principal
from app.services.workflow import transition

logger = get_logger(__name__)


def approvals_for_current_version(rap: Rap) -> dict[str, str]:
    """role -> reviewer name, for approvals of the CURRENT version only.

    Any revision creates a new version, so earlier approvals no longer count.
    """
    out: dict[str, str] = {}
    for r in rap.reviews:
        if r.rap_version_number == rap.current_version and r.decision == "approve":
            out.setdefault(r.reviewer_role, r.reviewer_name)
    return out


def approval_blockers(rap: Rap, settings: Settings) -> list[str]:
    blockers: list[str] = []
    if rap.status != "pending_review":
        blockers.append(f"rap is '{rap.status}', not 'pending_review'")
        return blockers
    version = current_version(rap)
    med, saf, ev = latest_validation(version, "medical"), latest_validation(version, "safety"), latest_evaluation(version)
    if med is None or saf is None or ev is None:
        blockers.append("current version has not been fully validated and evaluated")
    else:
        if med.status == "failed":
            blockers.append("unresolved critical/major medical accuracy errors")
        if saf.status == "failed":
            blockers.append("unresolved critical/major child-safety findings")
        failed_gates = [k for k, v in (ev.hard_gates or {}).items() if not v]
        if failed_gates:
            blockers.append(f"hard gates failed: {failed_gates}")
    open_critical = [
        i for r in rap.reviews if r.rap_version_number == rap.current_version and r.decision == "comment"
        for i in (r.issues or []) if i.get("severity") == "critical"
    ]
    if open_critical:
        blockers.append("a reviewer raised critical issues on this version; request a revision first")
    if settings.app_env == "production" and rap.topic.is_demo_data:
        blockers.append("demonstration data cannot be approved in production")
    return blockers


def review_rap(session: Session, rap: Rap, req: ReviewRequest, principal: Principal, settings: Settings) -> ReviewDecision:
    if principal.role not in PERMISSIONS["review"]:
        raise PermissionDeniedError("only medical reviewers and educators can review raps")
    if rap.status in {"approved", "rejected"} and req.decision != "comment":
        raise WorkflowError(f"rap is already '{rap.status}'")

    if req.decision == "approve":
        if principal.role not in settings.required_approval_roles:
            raise PermissionDeniedError(
                f"role '{principal.role}' is not a required approver ({settings.required_approval_roles})"
            )
        blockers = approval_blockers(rap, settings)
        if blockers:
            raise WorkflowError("approval blocked: " + "; ".join(blockers), details={"blockers": blockers})
        existing = approvals_for_current_version(rap)
        if principal.role in existing:
            raise WorkflowError(f"version {rap.current_version} already approved by a {principal.role}")
        if principal.name in existing.values():
            raise WorkflowError("the same person cannot provide more than one required approval")
    elif req.decision == "request_revision" and rap.status != "pending_review":
        raise WorkflowError(f"cannot request revision for a rap in status '{rap.status}'")
    elif req.decision == "reject" and rap.status not in {"pending_review", "revision_requested", "validation_failed", "draft"}:
        raise WorkflowError(f"cannot reject a rap in status '{rap.status}'")

    decision = ReviewDecision(
        rap_id=rap.id, rap_version_number=rap.current_version, reviewer_name=principal.name,
        reviewer_role=principal.role, decision=req.decision, feedback=req.feedback,
        issues=[i.model_dump() for i in req.issues],
    )
    rap.reviews.append(decision)
    session.flush()

    if req.decision == "approve":
        approvals = approvals_for_current_version(rap)
        missing = [r for r in settings.required_approval_roles if r not in approvals]
        if not missing:
            transition(session, rap, "approved", principal, f"approved by {sorted(approvals.items())}")
            rap.approved_at = datetime.now(timezone.utc)
            export_approved(rap, settings)
        log_event(logger, "rap_approval_recorded", rap_id=rap.id, role=principal.role, missing=len(missing))
    elif req.decision == "reject":
        transition(session, rap, "rejected", principal, "rejected by reviewer")
    elif req.decision == "request_revision":
        transition(session, rap, "revision_requested", principal, "reviewer requested revision")
    session.commit()
    return decision


def export_payload(rap: Rap) -> dict[str, Any]:
    if rap.status != "approved":
        raise WorkflowError("only approved raps can be exported")
    version = current_version(rap)
    return {
        "rap_id": rap.id,
        "topic": rap.topic.title,
        "topic_slug": rap.topic.slug,
        "age_band": rap.age_band,
        "version": version.version_number,
        "title": version.title,
        "lyrics": version.lyrics,
        "objectives_covered": version.objectives_covered,
        "approved_fact_keys": version.approved_fact_keys,
        "approvals": approvals_for_current_version(rap),
        "approved_at": rap.approved_at.isoformat() if rap.approved_at else None,
        "provenance": {
            "provider": version.provider, "model": version.model, "prompt_name": version.prompt_name,
            "prompt_version": version.prompt_version, "prompt_sha256": version.prompt_sha256,
            "run_id": version.run_id,
        },
        "is_demo_data": rap.topic.is_demo_data,
        "audio": None,  # reserved for the future licensed audio-generation stage
    }


def export_approved(rap: Rap, settings: Settings) -> Path:
    out_dir = Path(settings.outputs_dir) / "approved"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{rap.topic.slug}_rap{rap.id}_v{rap.current_version}.json"
    path.write_text(json.dumps(export_payload(rap), indent=2, default=str), encoding="utf-8")
    return path
