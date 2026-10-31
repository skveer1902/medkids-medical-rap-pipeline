"""Stage 2 - medical fact extraction and human fact approval.

The LLM only PROPOSES facts. Each proposal is checked deterministically
against the stored source text; anything that cannot be grounded is stored
as ``uncertain``. Nothing ever becomes ``approved`` except through
:func:`approve_facts`, which requires a human medical reviewer.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.exceptions import InputRejectedError, PermissionDeniedError
from app.core.logging import get_logger, log_event
from app.llm.base import LLMProvider
from app.models.database import Fact, Topic
from app.models.schemas import FactApprovalRequest, LLMFactExtractionOutput
from app.services.content_processor import detect_injection
from app.services.prompt_builder import build_fact_extraction_request
from app.services.security import Principal
from app.utils.text_utils import normalize_for_match, overlap_ratio

logger = get_logger(__name__)

FACT_APPROVER_ROLES = {"medical_reviewer"}


def compile_forbidden_patterns(topic: Topic) -> list[re.Pattern[str]]:
    patterns = []
    for fc in topic.forbidden_claims or []:
        if fc.get("pattern"):
            patterns.append(re.compile(fc["pattern"], re.IGNORECASE))
    return patterns


def _next_fact_key(topic: Topic) -> str:
    existing = [f.fact_key for f in topic.facts]
    n = len(existing) + 1
    while f"{topic.slug}_fact_{n:03d}" in existing:
        n += 1
    return f"{topic.slug}_fact_{n:03d}"


def approved_facts(topic: Topic) -> list[Fact]:
    return [f for f in topic.facts if f.status == "approved"]


class FactExtractor:
    def __init__(self, provider: LLMProvider, *, json_max_attempts: int = 3, max_facts: int = 15) -> None:
        self.provider = provider
        self.json_max_attempts = json_max_attempts
        self.max_facts = max_facts

    def extract(self, session: Session, topic: Topic) -> tuple[str, list[Fact]]:
        run_id = uuid.uuid4().hex
        sources = [
            {"source_id": s.source_key, "version": s.version, "content": s.content} for s in topic.sources
        ]
        if not sources:
            raise InputRejectedError("topic has no source documents to extract facts from")
        request = build_fact_extraction_request(
            topic=topic.title,
            age_min=topic.target_age_min,
            age_max=topic.target_age_max,
            sources=sources,
            objectives=[{"objective_id": o.objective_key, "text": o.text} for o in topic.objectives],
            forbidden_claims=[fc["claim"] for fc in topic.forbidden_claims or []],
            max_facts=self.max_facts,
        )
        result = self.provider.generate_json(request, LLMFactExtractionOutput, max_attempts=self.json_max_attempts)
        output: LLMFactExtractionOutput = result.data

        source_by_key = {s.source_key: s for s in topic.sources}
        forbidden = compile_forbidden_patterns(topic)
        seen = {normalize_for_match(f.text) for f in topic.facts}
        created: list[Fact] = []
        for proposal in output.facts[: self.max_facts]:
            norm_text = normalize_for_match(proposal.text)
            if not norm_text or norm_text in seen:
                continue
            seen.add(norm_text)
            reasons: list[str] = []
            src = source_by_key.get(proposal.source_id)
            verified = False
            if src is None:
                reasons.append(f"unknown source id '{proposal.source_id}'")
            else:
                quote = normalize_for_match(proposal.evidence_quote)
                if quote and quote in normalize_for_match(src.content):
                    verified = True
                else:
                    reasons.append("evidence quote not found verbatim in the source")
            if overlap_ratio(proposal.text, proposal.evidence_quote) < 0.5:
                reasons.append("fact wording is not grounded in its evidence quote")
            if detect_injection(proposal.evidence_quote) or detect_injection(proposal.text):
                reasons.append("evidence contains instruction-like text (possible prompt injection)")
            if any(p.search(proposal.text) for p in forbidden):
                reasons.append("matches a known misconception for this topic")
            if proposal.classification == "uncertain":
                reasons.append("model marked as uncertain" + (f": {proposal.note}" if proposal.note else ""))

            fact = Fact(
                topic_id=topic.id,
                fact_key=_next_fact_key(topic),
                text=proposal.text.strip(),
                source_document_id=src.id if src else None,
                evidence_quote=proposal.evidence_quote,
                evidence_verified=verified,
                status="uncertain" if reasons else "candidate",
                uncertainty_reason="; ".join(reasons) or None,
                proposed_by=f"{self.provider.name}:{self.provider.model}",
                extraction_run_id=run_id,
                is_demo_data=topic.is_demo_data,
            )
            topic.facts.append(fact)
            created.append(fact)
        session.flush()
        log_event(
            logger,
            "facts_extracted",
            topic_id=topic.id,
            run_id=run_id,
            candidates=sum(f.status == "candidate" for f in created),
            uncertain=sum(f.status == "uncertain" for f in created),
        )
        return run_id, created


def approve_facts(
    session: Session, topic: Topic, req: FactApprovalRequest, reviewer: Principal
) -> dict[str, list[str]]:
    """Human approval gate for facts. All-or-nothing: validates before applying."""
    if reviewer.role not in FACT_APPROVER_ROLES:
        raise PermissionDeniedError("only a medical reviewer can approve or reject medical facts")
    by_key = {f.fact_key: f for f in topic.facts}

    unknown = [k for k in req.approve_fact_ids + req.reject_fact_ids if k not in by_key]
    if unknown:
        raise InputRejectedError(f"unknown fact ids: {unknown}")
    not_approvable = [
        k for k in req.approve_fact_ids if by_key[k].status not in {"candidate", "approved"}
    ]
    if not_approvable:
        raise InputRejectedError(
            "these facts are uncertain or rejected and cannot be approved directly: "
            f"{not_approvable}. Add them as manual facts with a verified evidence quote instead.",
            details={"fact_ids": not_approvable},
        )
    source_by_key = {s.source_key: s for s in topic.sources}
    for mf in req.manual_facts:
        src = source_by_key.get(mf.source_key)
        if src is None:
            raise InputRejectedError(f"manual fact references unknown source '{mf.source_key}'")
        if normalize_for_match(mf.evidence_quote) not in normalize_for_match(src.content):
            raise InputRejectedError("manual fact evidence quote was not found verbatim in its source")

    now = datetime.now(timezone.utc)
    approved, rejected, added = [], [], []
    for key in req.approve_fact_ids:
        f = by_key[key]
        f.status, f.reviewed_by, f.reviewed_at, f.review_note = "approved", reviewer.name, now, req.note
        approved.append(key)
    for key in req.reject_fact_ids:
        f = by_key[key]
        f.status, f.reviewed_by, f.reviewed_at, f.review_note = "rejected", reviewer.name, now, req.note
        rejected.append(key)
    for mf in req.manual_facts:
        src = source_by_key[mf.source_key]
        fact = Fact(
            topic_id=topic.id,
            fact_key=_next_fact_key(topic),
            text=mf.text,
            source_document_id=src.id,
            evidence_quote=mf.evidence_quote,
            evidence_verified=True,
            status="approved",
            proposed_by=f"human:{reviewer.name}",
            reviewed_by=reviewer.name,
            reviewed_at=now,
            review_note=req.note,
            is_demo_data=topic.is_demo_data,
        )
        topic.facts.append(fact)
        session.flush()
        added.append(fact.fact_key)
    session.flush()
    log_event(logger, "facts_reviewed", topic_id=topic.id, reviewer=reviewer.name,
              approved=len(approved), rejected=len(rejected), added=len(added))
    return {"approved": approved, "rejected": rejected, "added": added}
