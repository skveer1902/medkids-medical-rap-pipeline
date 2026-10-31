"""End-to-end orchestration of the MEDKids rap pipeline.

    approved facts -> age adaptation -> prompt -> generation -> medical
    validation -> safety validation -> evaluation -> (capped targeted
    revision) -> pending human review

Generation and validation are separate operations with separate prompts.
Nothing in this module can approve a rap; see review_service.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import InputRejectedError, NotFoundError, WorkflowError
from app.core.logging import get_logger, log_event
from app.llm.base import LLMProvider
from app.models.database import (
    Evaluation,
    LLMCallLog,
    Rap,
    RapVersion,
    Topic,
    ValidationResult,
    WorkflowEvent,
)
from app.models.schemas import (
    EvaluationReport,
    GenerationSpec,
    Lyrics,
    MedicalAccuracyReport,
    RapGenerateRequest,
    SafetyReport,
)
from app.services import quality_evaluator
from app.services.age_adapter import AgeAdapter, resolve_age_band
from app.services.fact_extractor import approved_facts, compile_forbidden_patterns
from app.services.medical_validator import VALIDATOR_VERSION as MEDICAL_VERSION
from app.services.medical_validator import MedicalValidator
from app.services.rap_generator import RapGenerator
from app.services.revision_service import RevisionService, collect_problems
from app.services.safety_validator import VALIDATOR_VERSION as SAFETY_VERSION
from app.services.safety_validator import SafetyValidator
from app.services.security import Principal
from app.services.topic_service import get_topic
from app.services.workflow import transition

logger = get_logger(__name__)

SYSTEM_ACTOR = "system:pipeline"


@dataclass
class TopicContext:
    facts: list[dict[str, str]]
    objectives: list[dict[str, str]]
    forbidden: list[dict[str, Any]]

    @property
    def forbidden_texts(self) -> list[str]:
        return [f["claim"] for f in self.forbidden]


def topic_context(topic: Topic, fact_keys: list[str] | None = None) -> TopicContext:
    facts = approved_facts(topic)
    if fact_keys is not None:
        facts = [f for f in facts if f.fact_key in set(fact_keys)]
    return TopicContext(
        facts=[{"fact_id": f.fact_key, "text": f.text} for f in facts],
        objectives=[{"objective_id": o.objective_key, "text": o.text} for o in topic.objectives],
        forbidden=list(topic.forbidden_claims or []),
    )


def current_version(rap: Rap) -> RapVersion:
    for v in rap.versions:
        if v.version_number == rap.current_version:
            return v
    raise NotFoundError(f"rap {rap.id} has no version {rap.current_version}")


def latest_validation(version: RapVersion, kind: str) -> ValidationResult | None:
    found = [v for v in version.validations if v.kind == kind]
    return found[-1] if found else None


def latest_evaluation(version: RapVersion) -> Evaluation | None:
    return version.evaluations[-1] if version.evaluations else None


def get_rap(session: Session, rap_id: int) -> Rap:
    rap = session.get(Rap, rap_id)
    if rap is None:
        raise NotFoundError(f"rap {rap_id} not found")
    return rap


class Pipeline:
    def __init__(self, settings: Settings, provider: LLMProvider) -> None:
        self.settings = settings
        self.provider = provider
        n = settings.llm_json_max_attempts
        self.age_adapter = AgeAdapter(provider, json_max_attempts=n)
        self.generator = RapGenerator(
            provider, temperature=settings.llm_temperature, max_tokens=settings.llm_max_tokens,
            json_max_attempts=n,
            extra_config={"claude_effort": settings.claude_effort} if provider.name == "claude" else None,
        )
        self.medical = MedicalValidator(provider, json_max_attempts=n)
        self.safety = SafetyValidator(provider, json_max_attempts=n)
        self.reviser = RevisionService(provider, temperature=settings.llm_temperature, json_max_attempts=n)

    # -- bookkeeping --------------------------------------------------------

    def _persist_llm_calls(self, session: Session, run_id: str) -> None:
        for c in self.provider.usage.drain():
            session.add(LLMCallLog(
                run_id=run_id, task=c.task, provider=c.provider, model=c.model, prompt_name=c.prompt_name,
                prompt_version=c.prompt_version, input_tokens=c.input_tokens, output_tokens=c.output_tokens,
                estimated_cost_usd=c.estimated_cost_usd, latency_ms=c.latency_ms, attempts=c.attempts,
                success=c.success, error_code=c.error_code,
            ))

    # -- generation --------------------------------------------------------

    def generate(self, session: Session, req: RapGenerateRequest, principal: Principal) -> list[Rap]:
        topic = get_topic(session, req.topic_id)
        if req.num_candidates > self.settings.max_candidates_per_request:
            raise InputRejectedError(
                f"num_candidates must be <= {self.settings.max_candidates_per_request}"
            )
        ctx = topic_context(topic)
        if not ctx.facts:
            raise WorkflowError(
                "topic has no approved facts; extract candidates and have a medical reviewer approve them first"
            )
        band = resolve_age_band(topic.target_age_min, topic.target_age_max, req.age_band)
        simplified, notes = self.age_adapter.simplify_facts(
            topic=topic.title, band=band, facts=ctx.facts, objectives=ctx.objectives,
            forbidden_patterns=compile_forbidden_patterns(topic),
        )
        spec = GenerationSpec(
            topic_title=topic.title, age_band=band, style=req.style, num_verses=req.num_verses,
            chorus=req.chorus, include_intro=req.include_intro, include_outro=req.include_outro,
            approved_facts=simplified, learning_objectives=ctx.objectives,
            forbidden_claims=ctx.forbidden_texts, required_vocabulary=list(topic.required_vocabulary or []),
        )
        raps: list[Rap] = []
        for _ in range(req.num_candidates):
            gen = self.generator.generate(spec)
            rap = Rap(
                topic_id=topic.id, status="draft", age_band=band, style=req.style,
                generation_request={**req.model_dump(), "simplification_notes": notes,
                                    "simplified_facts": simplified},
                current_version=1, created_by=principal.name,
            )
            rap.versions.append(RapVersion(
                version_number=1, title=gen.lyrics.title, lyrics=gen.lyrics.model_dump(),
                objectives_covered=gen.objectives_covered, change_type="generated", changed_line_ids=[],
                change_reason="; ".join(gen.notes) or None, provider=gen.provider, model=gen.model,
                prompt_name=gen.prompt_name, prompt_version=gen.prompt_version, prompt_sha256=gen.prompt_sha256,
                generation_config=gen.generation_config, approved_fact_keys=[f["fact_id"] for f in ctx.facts],
                run_id=gen.run_id, input_tokens=gen.input_tokens, output_tokens=gen.output_tokens,
                estimated_cost_usd=gen.estimated_cost_usd,
            ))
            session.add(rap)
            session.flush()
            session.add(WorkflowEvent(rap_id=rap.id, from_status=None, to_status="draft", actor=principal.name,
                                      reason="generated", version_number=1))
            self._persist_llm_calls(session, gen.run_id)
            session.commit()
            if req.run_pipeline:
                self.run_checks(session, rap, auto_revise=req.auto_revise)
            raps.append(rap)
        return raps

    # -- validation ----------------------------------------------------------

    def validate(self, session: Session, rap: Rap) -> tuple[MedicalAccuracyReport, SafetyReport]:
        if rap.status not in {"draft", "validation_failed", "pending_review"}:
            raise WorkflowError(f"cannot validate a rap in status '{rap.status}'")
        version = current_version(rap)
        topic = rap.topic
        ctx = topic_context(topic, version.approved_fact_keys)
        lyrics = Lyrics.model_validate(version.lyrics)
        medical = self.medical.validate(topic=topic.title, band=rap.age_band, lyrics=lyrics,
                                        facts=ctx.facts, forbidden_claims=ctx.forbidden)
        safety = self.safety.validate(topic=topic.title, band=rap.age_band, lyrics=lyrics,
                                      required_vocabulary=list(topic.required_vocabulary or []))
        version.validations.append(ValidationResult(kind="medical", status=medical.status,
                                                    report=medical.model_dump(), validator_version=MEDICAL_VERSION,
                                                    run_id=version.run_id))
        version.validations.append(ValidationResult(kind="safety", status=safety.status,
                                                    report=safety.model_dump(), validator_version=SAFETY_VERSION,
                                                    run_id=version.run_id))
        if medical.status == "failed" or safety.status == "failed":
            transition(session, rap, "validation_failed", SYSTEM_ACTOR,
                       f"medical={medical.status}, safety={safety.status}")
        elif rap.status == "validation_failed":
            transition(session, rap, "draft", SYSTEM_ACTOR, "re-validation passed")
        self._persist_llm_calls(session, version.run_id)
        session.commit()
        return medical, safety

    def evaluate(self, session: Session, rap: Rap) -> EvaluationReport:
        if rap.status not in {"draft", "validation_failed", "pending_review"}:
            raise WorkflowError(f"cannot evaluate a rap in status '{rap.status}'")
        version = current_version(rap)
        med_row, saf_row = latest_validation(version, "medical"), latest_validation(version, "safety")
        if med_row is None or saf_row is None:
            raise WorkflowError("validate the current version before evaluating it")
        medical = MedicalAccuracyReport.model_validate(med_row.report)
        safety = SafetyReport.model_validate(saf_row.report)
        ctx = topic_context(rap.topic, version.approved_fact_keys)
        report = quality_evaluator.evaluate(
            lyrics=Lyrics.model_validate(version.lyrics), topic=rap.topic.title, band=rap.age_band,
            objectives=ctx.objectives, declared_objectives=list(version.objectives_covered or []),
            medical=medical, safety=safety,
            num_verses=int(rap.generation_request.get("num_verses", 2)),
            weights=self.settings.score_weights, threshold=self.settings.soft_score_pass_threshold,
        )
        version.evaluations.append(Evaluation(
            total_score=report.total_score, scores={k: v.model_dump() for k, v in report.scores.items()},
            hard_gates=report.hard_gates, automated_checks_passed=report.automated_checks_passed,
            recommendations=report.recommendations, rubric_version=report.rubric_version,
            weights=dict(self.settings.score_weights), threshold=report.threshold,
        ))
        if all(report.hard_gates.values()):
            if rap.status == "draft":
                note = "automated checks passed" if report.soft_score_passed else (
                    f"hard gates passed; soft score {report.total_score} below threshold {report.threshold}")
                transition(session, rap, "pending_review", SYSTEM_ACTOR, note)
        elif rap.status in {"draft", "pending_review"}:
            failed = [k for k, v in report.hard_gates.items() if not v]
            transition(session, rap, "validation_failed", SYSTEM_ACTOR, f"hard gates failed: {failed}")
        session.commit()
        return report

    # -- revision ------------------------------------------------------------

    def revise(self, session: Session, rap: Rap, actor: Principal | str, *, auto_validate: bool = True) -> bool:
        """Create a targeted revision. Returns True if any line changed."""
        version = current_version(rap)
        med_row, saf_row = latest_validation(version, "medical"), latest_validation(version, "safety")
        medical = MedicalAccuracyReport.model_validate(med_row.report) if med_row else None
        safety = SafetyReport.model_validate(saf_row.report) if saf_row else None

        if rap.status == "revision_requested":
            change_type = "reviewer_revision"
            issues = [i for r in rap.reviews if r.rap_version_number == version.version_number
                      and r.decision in {"request_revision", "comment"} for i in (r.issues or [])]
            problems = collect_problems(medical, safety, issues)
            if not problems:
                raise InputRejectedError(
                    "the revision request has no line-level issues to fix; add issues with line_id "
                    "or generate a new rap"
                )
        elif rap.status == "validation_failed":
            change_type = "auto_revision"
            if rap.auto_revision_attempts >= self.settings.max_revision_attempts:
                rap.needs_human_intervention = True
                session.commit()
                raise WorkflowError(
                    f"automatic revision limit ({self.settings.max_revision_attempts}) reached; "
                    "human intervention required"
                )
            problems = collect_problems(medical, safety)
            if not problems:
                rap.needs_human_intervention = True
                session.commit()
                raise WorkflowError("validation failures are not tied to specific lines; human intervention required")
            rap.auto_revision_attempts += 1
        else:
            raise WorkflowError(f"cannot revise a rap in status '{rap.status}'")

        ctx = topic_context(rap.topic, version.approved_fact_keys)
        simplified = {f["fact_id"]: f.get("simplified_text", f["text"])
                      for f in rap.generation_request.get("simplified_facts", [])}
        facts = [{**f, "simplified_text": simplified.get(f["fact_id"], f["text"])} for f in ctx.facts]
        result = self.reviser.revise(
            topic=rap.topic.title, band=rap.age_band, lyrics=Lyrics.model_validate(version.lyrics),
            facts=facts, forbidden_claims=ctx.forbidden_texts, problems=problems,
        )
        if not result.changed_line_ids:
            log_event(logger, "revision_no_change", rap_id=rap.id, attempt=rap.auto_revision_attempts)
            self._persist_llm_calls(session, version.run_id)
            session.commit()
            return False
        new_number = version.version_number + 1
        rap.versions.append(RapVersion(
            version_number=new_number, title=result.lyrics.title, lyrics=result.lyrics.model_dump(),
            objectives_covered=list(version.objectives_covered or []), change_type=change_type,
            changed_line_ids=result.changed_line_ids,
            change_reason="; ".join(f"{k}: {' | '.join(v)}" for k, v in problems.items()
                                    if k in result.changed_line_ids)[:4000],
            provider=self.provider.name, model=result.model, prompt_name=result.prompt_name,
            prompt_version=result.prompt_version, prompt_sha256=result.prompt_sha256,
            generation_config={**version.generation_config, "revision_of": version.version_number,
                               "ignored_line_ids": result.ignored_line_ids},
            approved_fact_keys=list(version.approved_fact_keys), run_id=version.run_id,
            input_tokens=result.input_tokens, output_tokens=result.output_tokens,
            estimated_cost_usd=result.estimated_cost_usd,
        ))
        rap.current_version = new_number
        actor_name = actor.name if isinstance(actor, Principal) else actor
        transition(session, rap, "draft", actor_name, f"{change_type} -> v{new_number}")
        self._persist_llm_calls(session, version.run_id)
        session.commit()
        if auto_validate:
            self.validate(session, rap)
            self.evaluate(session, rap)
        return True

    def run_checks(self, session: Session, rap: Rap, *, auto_revise: bool = True) -> Rap:
        """Validate + evaluate, then auto-revise at most MAX_REVISION_ATTEMPTS times."""
        self.validate(session, rap)
        self.evaluate(session, rap)
        while auto_revise and rap.status == "validation_failed":
            if rap.auto_revision_attempts >= self.settings.max_revision_attempts:
                rap.needs_human_intervention = True
                session.commit()
                log_event(logger, "revision_limit_reached", rap_id=rap.id)
                break
            try:
                changed = self.revise(session, rap, SYSTEM_ACTOR, auto_validate=False)
            except WorkflowError:
                break  # needs_human_intervention already set
            if changed:
                self.validate(session, rap)
                self.evaluate(session, rap)
        return rap
