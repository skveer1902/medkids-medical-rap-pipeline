"""Pydantic v2 schemas: API contracts, internal lyric structures and the JSON
contracts every LLM response must satisfy before it is used."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --------------------------------------------------------------------------
# Shared enums / constants
# --------------------------------------------------------------------------

SUPPORTED_CATEGORIES = (
    "Human Anatomy",
    "Body Parts",
    "Body Systems",
    "Organ Functions",
    "Medical Professions",
    "Health Education",
)
Category = Literal[
    "Human Anatomy",
    "Body Parts",
    "Body Systems",
    "Organ Functions",
    "Medical Professions",
    "Health Education",
]
AgeBand = Literal["4-6", "7-10"]
RapStyle = Literal["playful", "call_and_response", "classic_boom_bap", "chant"]
RapStatus = Literal[
    "draft", "validation_failed", "pending_review", "revision_requested", "approved", "rejected"
]
FactStatus = Literal["candidate", "uncertain", "approved", "rejected"]
ClaimStatus = Literal["supported", "contradicted", "unsupported", "ambiguous", "outside_scope"]
Severity = Literal["critical", "major", "minor"]
SafetyCategory = Literal[
    "graphic_medical",
    "frightening",
    "inappropriate_vocabulary",
    "unsafe_health_advice",
    "stereotype",
    "discriminatory",
    "misleading_health_claim",
    "unnecessary_complexity",
    "structure",
    "copyright_imitation",
    "other",
]
ValidationStatus = Literal["passed", "requires_review", "failed"]

_TOPIC_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 '\-]{1,79}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --------------------------------------------------------------------------
# Topic input (Stage 1)
# --------------------------------------------------------------------------


class ForbiddenClaimIn(StrictModel):
    claim: str = Field(min_length=3, max_length=300, description="A known misconception to block.")
    pattern: str | None = Field(
        default=None,
        max_length=200,
        description="Optional case-insensitive regex that detects the misconception in a lyric line.",
    )

    @field_validator("pattern")
    @classmethod
    def _valid_regex(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            re.compile(v)
        except re.error as exc:
            raise ValueError(f"invalid regex: {exc}") from exc
        return v


class TopicCreate(StrictModel):
    topic: str = Field(min_length=2, max_length=80)
    category: Category
    target_age_min: int = Field(ge=4, le=10)
    target_age_max: int = Field(ge=4, le=10)
    educational_content: str = Field(min_length=1, max_length=200000)
    source_title: str | None = Field(default=None, max_length=200)
    source_uri: str | None = Field(default=None, max_length=500)
    source_version: str = Field(default="1", max_length=40)
    source_is_approved: bool = Field(
        default=False,
        description="Set true only when the content comes from an approved MEDKids source document.",
    )
    primary_objective: str | None = Field(default=None, max_length=300)
    learning_objectives: list[str] = Field(min_length=1, max_length=12)
    forbidden_claims: list[ForbiddenClaimIn] = Field(default_factory=list, max_length=20)
    required_vocabulary: list[str] = Field(default_factory=list, max_length=20)
    is_demo_data: bool = False

    @field_validator("topic")
    @classmethod
    def _topic_name(cls, v: str) -> str:
        if not _TOPIC_NAME_RE.match(v):
            raise ValueError("topic must start with a letter and contain only letters, digits, spaces, ' or -")
        return v

    @field_validator("educational_content")
    @classmethod
    def _non_empty_content(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("educational_content must not be empty")
        return v

    @field_validator("learning_objectives")
    @classmethod
    def _objectives(cls, v: list[str]) -> list[str]:
        cleaned = [o.strip() for o in v]
        for o in cleaned:
            if len(o) < 3 or len(o) > 300:
                raise ValueError("each learning objective must be 3-300 characters")
        if len(set(o.lower() for o in cleaned)) != len(cleaned):
            raise ValueError("learning objectives must be unique")
        return cleaned

    @field_validator("required_vocabulary")
    @classmethod
    def _vocab(cls, v: list[str]) -> list[str]:
        out = [w.strip().lower() for w in v if w.strip()]
        for w in out:
            if len(w) > 40:
                raise ValueError("vocabulary terms must be at most 40 characters")
        return out

    @model_validator(mode="after")
    def _age_range(self) -> TopicCreate:
        if self.target_age_min > self.target_age_max:
            raise ValueError("target_age_min must be <= target_age_max")
        return self


class SourceDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    source_key: str
    title: str
    uri: str | None
    version: str
    content: str
    content_sha256: str
    is_approved_source: bool
    created_at: datetime


class ObjectiveOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    objective_key: str
    text: str
    position: int


class FactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    fact_key: str
    text: str
    status: FactStatus
    source_document_id: int | None
    evidence_quote: str | None
    evidence_verified: bool
    uncertainty_reason: str | None
    proposed_by: str
    reviewed_by: str | None
    reviewed_at: datetime | None
    is_demo_data: bool


class TopicOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    slug: str
    title: str
    category: str
    target_age_min: int
    target_age_max: int
    primary_objective: str | None
    forbidden_claims: list[dict[str, Any]]
    required_vocabulary: list[str]
    content_flags: list[dict[str, Any]]
    is_demo_data: bool
    created_by: str
    created_at: datetime
    objectives: list[ObjectiveOut]
    sources: list[SourceDocumentOut]
    facts: list[FactOut]


class TopicSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    slug: str
    title: str
    category: str
    target_age_min: int
    target_age_max: int
    is_demo_data: bool
    created_at: datetime


# --------------------------------------------------------------------------
# Fact extraction & approval (Stage 2)
# --------------------------------------------------------------------------


class FactExtractionResponse(BaseModel):
    topic_id: int
    run_id: str
    candidates: list[FactOut]
    uncertain: list[FactOut]
    note: str = (
        "Candidate facts are NOT approved. A medical reviewer must approve them via "
        "POST /api/topics/{topic_id}/facts/approve before they can be used for generation."
    )


class ManualFactIn(StrictModel):
    text: str = Field(min_length=5, max_length=400)
    source_key: str = Field(min_length=1, max_length=80)
    evidence_quote: str = Field(min_length=5, max_length=1000)


class FactApprovalRequest(StrictModel):
    approve_fact_ids: list[str] = Field(default_factory=list, max_length=100)
    reject_fact_ids: list[str] = Field(default_factory=list, max_length=100)
    manual_facts: list[ManualFactIn] = Field(default_factory=list, max_length=30)
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _non_empty(self) -> FactApprovalRequest:
        if not (self.approve_fact_ids or self.reject_fact_ids or self.manual_facts):
            raise ValueError("provide at least one fact to approve, reject or add")
        if set(self.approve_fact_ids) & set(self.reject_fact_ids):
            raise ValueError("a fact cannot be both approved and rejected")
        return self


class FactApprovalResponse(BaseModel):
    topic_id: int
    approved: list[str]
    rejected: list[str]
    added: list[str]
    approved_fact_count: int


# --------------------------------------------------------------------------
# Lyrics structures
# --------------------------------------------------------------------------


class LyricLine(BaseModel):
    line_id: str
    text: str
    fact_ids: list[str] = Field(default_factory=list)


class LyricSection(BaseModel):
    section: str
    lines: list[LyricLine]


class Lyrics(BaseModel):
    title: str
    sections: list[LyricSection]
    arrangement: list[str] = Field(default_factory=list, description="Performance order of sections.")

    def all_lines(self) -> list[LyricLine]:
        return [line for s in self.sections for line in s.lines]

    def line_map(self) -> dict[str, LyricLine]:
        return {line.line_id: line for line in self.all_lines()}

    def section_of(self, line_id: str) -> str | None:
        for s in self.sections:
            if any(line.line_id == line_id for line in s.lines):
                return s.section
        return None

    def text(self) -> str:
        return "\n".join(line.text for line in self.all_lines())


# --------------------------------------------------------------------------
# Generation (Stage 4)
# --------------------------------------------------------------------------


class ChorusRequirements(StrictModel):
    max_lines: int = Field(default=4, ge=2, le=6)
    repeat_times: int = Field(default=2, ge=1, le=3)
    include_topic_name: bool = True


class RapGenerateRequest(StrictModel):
    topic_id: int
    age_band: AgeBand | None = None
    style: RapStyle = "playful"
    num_verses: int = Field(default=2, ge=2, le=4)
    chorus: ChorusRequirements = Field(default_factory=ChorusRequirements)
    include_intro: bool = False
    include_outro: bool = False
    num_candidates: int = Field(default=1, ge=1, le=3)
    run_pipeline: bool = Field(
        default=True, description="Run validation, evaluation and capped auto-revision after generation."
    )
    auto_revise: bool = True


class GenerationSpec(BaseModel):
    """Everything the generator needs, resolved from the topic and request."""

    topic_title: str
    age_band: AgeBand
    style: RapStyle
    num_verses: int
    chorus: ChorusRequirements
    include_intro: bool
    include_outro: bool
    approved_facts: list[dict[str, str]]  # {fact_id, text, simplified_text}
    learning_objectives: list[dict[str, str]]  # {objective_id, text}
    forbidden_claims: list[str]
    required_vocabulary: list[str]


# --------------------------------------------------------------------------
# Validation & evaluation reports
# --------------------------------------------------------------------------


class ClaimResult(BaseModel):
    line_id: str | None
    claim: str
    status: ClaimStatus
    source_fact_id: str | None
    severity: Severity | None = None
    rationale: str = ""
    checks: list[str] = Field(default_factory=list)


class MedicalAccuracyReport(BaseModel):
    status: ValidationStatus
    claims: list[ClaimResult]
    failing_line_ids: list[str]
    review_line_ids: list[str]
    summary: dict[str, int]
    automated_triage_available: bool = True
    disclaimer: str = (
        "Automated claim checking is triage only. It is not an independent medical authority; "
        "a human medical reviewer must sign off."
    )


class SafetyFinding(BaseModel):
    line_id: str | None
    category: SafetyCategory
    severity: Severity
    explanation: str
    suggestion: str | None = None
    source: Literal["rule", "llm"]


class SafetyReport(BaseModel):
    status: ValidationStatus
    findings: list[SafetyFinding]
    failing_line_ids: list[str]
    llm_review_available: bool = True


class CriterionScore(BaseModel):
    score: float
    weight: float
    explanation: str


class EvaluationReport(BaseModel):
    total_score: float
    scores: dict[str, CriterionScore]
    hard_gates: dict[str, bool]
    soft_score_passed: bool
    automated_checks_passed: bool
    threshold: float
    recommendations: list[str]
    rubric_version: str
    note: str = (
        "Rubric weights and thresholds are experimental pilot values. A passing score only "
        "moves a rap to human review; it never approves it."
    )


# --------------------------------------------------------------------------
# Review (Stage 9)
# --------------------------------------------------------------------------


class ReviewIssueIn(StrictModel):
    line_id: str | None = Field(default=None, max_length=40)
    severity: Severity = "major"
    reason: str = Field(min_length=3, max_length=500)


class ReviewRequest(StrictModel):
    decision: Literal["approve", "reject", "request_revision", "comment"]
    feedback: str | None = Field(default=None, max_length=2000)
    issues: list[ReviewIssueIn] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def _feedback_required(self) -> ReviewRequest:
        if self.decision in {"reject", "request_revision", "comment"} and not (
            self.feedback or self.issues
        ):
            raise ValueError(f"'{self.decision}' requires feedback or issues")
        return self


class ReviseRequest(StrictModel):
    auto_validate: bool = True


class ReviewDecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    rap_version_number: int
    reviewer_name: str
    reviewer_role: str
    decision: str
    feedback: str | None
    issues: list[dict[str, Any]]
    created_at: datetime


class RapVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    version_number: int
    title: str
    lyrics: dict[str, Any]
    objectives_covered: list[str]
    change_type: str
    changed_line_ids: list[str]
    change_reason: str | None
    provider: str
    model: str
    prompt_name: str
    prompt_version: str
    prompt_sha256: str
    generation_config: dict[str, Any]
    approved_fact_keys: list[str]
    run_id: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float | None
    created_at: datetime


class RapSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    topic_id: int
    status: RapStatus
    age_band: str
    style: str
    current_version: int
    needs_human_intervention: bool
    created_at: datetime
    updated_at: datetime


class RapDetail(RapSummary):
    title: str
    lyrics: dict[str, Any]
    objectives_covered: list[str]
    medical_validation: dict[str, Any] | None
    safety_validation: dict[str, Any] | None
    evaluation: dict[str, Any] | None
    reviews: list[ReviewDecisionOut]
    approvals_for_current_version: list[str]
    approvals_required: list[str]
    auto_revision_attempts: int
    is_demo_data: bool


class WorkflowEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    from_status: str | None
    to_status: str
    actor: str
    reason: str | None
    version_number: int
    created_at: datetime


class RapHistory(BaseModel):
    rap_id: int
    status: RapStatus
    versions: list[RapVersionOut]
    events: list[WorkflowEventOut]
    reviews: list[ReviewDecisionOut]
    validations: list[dict[str, Any]]
    evaluations: list[dict[str, Any]]


class GenerateResponse(BaseModel):
    raps: list[RapDetail]


# --------------------------------------------------------------------------
# LLM output contracts (validated before use)
# --------------------------------------------------------------------------


class LLMExtractedFact(BaseModel):
    text: str = Field(min_length=3, max_length=400)
    evidence_quote: str = Field(min_length=3, max_length=1000)
    source_id: str = Field(min_length=1, max_length=80)
    classification: Literal["supported", "uncertain"]
    note: str | None = Field(default=None, max_length=500)


class LLMFactExtractionOutput(BaseModel):
    facts: list[LLMExtractedFact] = Field(max_length=40)


class LLMSimplifiedFact(BaseModel):
    fact_id: str
    simplified_text: str = Field(min_length=3, max_length=300)


class LLMSimplificationOutput(BaseModel):
    items: list[LLMSimplifiedFact]


class LLMLyricLine(BaseModel):
    text: str = Field(min_length=1, max_length=200)
    fact_ids: list[str] = Field(default_factory=list, max_length=10)


class LLMLyricSection(BaseModel):
    section: str = Field(pattern=r"^(intro|verse_[1-4]|chorus|outro)$")
    lines: list[LLMLyricLine] = Field(min_length=1, max_length=16)


class LLMRapOutput(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    sections: list[LLMLyricSection] = Field(min_length=2, max_length=10)
    objectives_covered: list[str] = Field(default_factory=list, max_length=12)


class LLMClaim(BaseModel):
    line_id: str
    claim: str = Field(min_length=2, max_length=300)
    status: ClaimStatus
    fact_id: str | None = None
    rationale: str = Field(default="", max_length=500)


class LLMMedicalValidationOutput(BaseModel):
    claims: list[LLMClaim] = Field(max_length=80)


class LLMSafetyFinding(BaseModel):
    line_id: str | None = None
    category: SafetyCategory
    severity: Severity
    explanation: str = Field(min_length=2, max_length=500)
    suggestion: str | None = Field(default=None, max_length=300)


class LLMSafetyOutput(BaseModel):
    findings: list[LLMSafetyFinding] = Field(max_length=40)


class LLMRevisedLine(BaseModel):
    line_id: str
    text: str = Field(min_length=1, max_length=200)
    fact_ids: list[str] = Field(default_factory=list, max_length=10)


class LLMRevisionOutput(BaseModel):
    revised_lines: list[LLMRevisedLine] = Field(max_length=40)
    notes: str | None = Field(default=None, max_length=1000)


class HealthResponse(BaseModel):
    status: Literal["ok"]
    app_env: str
    llm_provider: str
    database: Literal["ok", "error"]
    version: str
    local_mode: bool = False  # True when the no-keys local login (AUTH_MODE=dev) is on
