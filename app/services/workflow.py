"""Rap review-status state machine.

    draft --validate/evaluate--> pending_review | validation_failed
    validation_failed --revise--> draft
    pending_review --human--> approved | rejected | revision_requested
    pending_review --re-validation fails--> validation_failed
    revision_requested --revise--> draft ; --human--> rejected
    approved, rejected: terminal

``approved`` can only be entered through :func:`transition` with a human
reviewer principal; no automated code path passes one.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import REVIEWER_ROLES
from app.core.exceptions import WorkflowError
from app.core.logging import get_logger, log_event
from app.models.database import Rap, WorkflowEvent
from app.services.security import Principal

logger = get_logger(__name__)

ALLOWED: dict[str, set[str]] = {
    "draft": {"pending_review", "validation_failed", "rejected"},
    "validation_failed": {"draft", "rejected"},
    "pending_review": {"approved", "rejected", "revision_requested", "validation_failed"},
    "revision_requested": {"draft", "rejected"},
    "approved": set(),
    "rejected": set(),
}
HUMAN_ONLY = {"approved", "rejected", "revision_requested"}


def transition(session: Session, rap: Rap, to_status: str, actor: Principal | str, reason: str | None = None) -> None:
    from_status = rap.status
    if to_status == from_status:
        return
    if to_status not in ALLOWED.get(from_status, set()):
        raise WorkflowError(
            f"cannot move rap {rap.id} from '{from_status}' to '{to_status}'",
            details={"from": from_status, "to": to_status},
        )
    if to_status in HUMAN_ONLY:
        if not isinstance(actor, Principal) or actor.role not in REVIEWER_ROLES:
            raise WorkflowError(f"'{to_status}' requires a human reviewer decision")
    actor_name = actor.name if isinstance(actor, Principal) else actor
    rap.status = to_status
    session.add(
        WorkflowEvent(rap_id=rap.id, from_status=from_status, to_status=to_status, actor=actor_name,
                      reason=reason, version_number=rap.current_version)
    )
    log_event(logger, "rap_transition", rap_id=rap.id, from_status=from_status, to_status=to_status)
