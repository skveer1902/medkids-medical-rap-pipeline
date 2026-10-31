"""Stage 5 - source-grounded medical accuracy validation.

Layered design (the LLM is a triage assistant, never the authority):

1. Deterministic misconception scan (topic forbidden-claim regexes + a small
   global list of well-known misconceptions) -> ``contradicted`` / critical.
2. LLM claim extraction & classification against the APPROVED facts only.
   Its output is post-checked: line ids must exist, cited fact ids must be
   approved, "supported" requires a fact id and lexical grounding, otherwise
   it is downgraded to ``ambiguous`` (human review). Confidence scores are
   neither requested nor used.
3. Coverage guard: any line containing body/health vocabulary that received
   no claim is added as ``ambiguous`` so an LLM omission cannot pass silently.
4. If the LLM call fails, a deterministic fallback runs and the report can
   at best be ``requires_review``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.exceptions import LLMError
from app.core.logging import get_logger, log_event
from app.llm.base import LLMProvider
from app.models.schemas import (
    ClaimResult,
    LLMMedicalValidationOutput,
    Lyrics,
    MedicalAccuracyReport,
)
from app.services.lexicon import is_medical_statement
from app.services.prompt_builder import build_medical_validation_request
from app.utils.text_utils import overlap_ratio

logger = get_logger(__name__)

VALIDATOR_VERSION = "1.0.0"
SUPPORT_OVERLAP_MIN = 0.25  # lexical sanity check for LLM-"supported" claims
FALLBACK_SUPPORT_OVERLAP = 0.5

# Widely documented misconceptions that should never appear, whatever the topic.
GLOBAL_MISCONCEPTIONS: list[tuple[str, re.Pattern[str]]] = [
    ("The heart makes/produces blood (blood is made mainly in bone marrow)",
     re.compile(r"\bheart\w*\b.{0,20}\b(makes?|making|produces?|creates?)\b.{0,10}\bblood\b", re.I)),
    ("The heart cleans or filters the blood",
     re.compile(r"\bheart\w*\b.{0,20}\b(clean|cleans|filter|filters|cleaning|filtering)\b.{0,10}\bblood\b", re.I)),
    ("Blood inside the body is blue",
     re.compile(r"\bblood\b.{0,15}\b(is|turns|looks)\b.{0,10}\bblue\b", re.I)),
    ("People only use 10% of their brain",
     re.compile(r"\b(only|just)\b.{0,15}\b(10|ten)\s*(%|percent)\b.{0,20}\bbrain", re.I)),
    ("The brain is a muscle",
     re.compile(r"\bbrain\b\s*(is|'s)\s+(a\s+)?muscle\b", re.I)),
]


@dataclass
class ForbiddenRule:
    claim: str
    pattern: re.Pattern[str]


def build_rules(forbidden_claims: list[dict]) -> list[ForbiddenRule]:
    rules = [ForbiddenRule(c, p) for c, p in GLOBAL_MISCONCEPTIONS]
    for fc in forbidden_claims:
        if fc.get("pattern"):
            rules.append(ForbiddenRule(fc["claim"], re.compile(fc["pattern"], re.I)))
    return rules


_SEVERITY = {"contradicted": "critical", "unsupported": "major", "ambiguous": "minor", "outside_scope": "minor"}


class MedicalValidator:
    def __init__(self, provider: LLMProvider, *, json_max_attempts: int = 3) -> None:
        self.provider = provider
        self.json_max_attempts = json_max_attempts

    def validate(
        self,
        *,
        topic: str,
        band: str,
        lyrics: Lyrics,
        facts: list[dict[str, str]],
        forbidden_claims: list[dict],
    ) -> MedicalAccuracyReport:
        fact_by_id = {f["fact_id"]: f for f in facts}
        line_map = lyrics.line_map()
        claims: list[ClaimResult] = []

        # 1) deterministic misconception scan
        for line in lyrics.all_lines():
            for rule in build_rules(forbidden_claims):
                if rule.pattern.search(line.text):
                    claims.append(
                        ClaimResult(
                            line_id=line.line_id,
                            claim=line.text,
                            status="contradicted",
                            source_fact_id=None,
                            rationale=f"States a known misconception: {rule.claim}",
                            checks=["forbidden_claim_rule"],
                        )
                    )

        # 2) LLM triage with deterministic post-checks
        triage_ok = True
        try:
            request = build_medical_validation_request(
                topic=topic,
                band=band,
                lyrics=lyrics,
                facts=facts,
                forbidden_claims=[fc["claim"] for fc in forbidden_claims],
            )
            out: LLMMedicalValidationOutput = self.provider.generate_json(
                request, LLMMedicalValidationOutput, max_attempts=self.json_max_attempts
            ).data
            for c in out.claims:
                if c.line_id not in line_map:
                    continue  # hallucinated line id; coverage guard below still applies
                checks: list[str] = []
                status = c.status
                fact_id = c.fact_id
                if fact_id is not None and fact_id not in fact_by_id:
                    checks.append(f"cited fact id '{fact_id}' is not an approved fact")
                    fact_id = None
                    if status == "supported":
                        status = "ambiguous"
                if status == "supported":
                    if fact_id is None:
                        status = "ambiguous"
                        checks.append("marked supported without an approved fact id")
                    else:
                        grounding = max(
                            overlap_ratio(c.claim, fact_by_id[fact_id]["text"]),
                            overlap_ratio(line_map[c.line_id].text, fact_by_id[fact_id]["text"]),
                        )
                        if grounding < SUPPORT_OVERLAP_MIN:
                            status = "ambiguous"
                            checks.append(f"weak lexical grounding ({grounding:.2f}) to {fact_id}")
                claims.append(
                    ClaimResult(
                        line_id=c.line_id,
                        claim=c.claim,
                        status=status,
                        source_fact_id=fact_id if status == "supported" else None,
                        rationale=c.rationale,
                        checks=checks + ["llm_triage"],
                    )
                )
        except LLMError as exc:
            triage_ok = False
            log_event(logger, "medical_triage_unavailable", error=exc.code)
            claims.extend(self._fallback_claims(lyrics, fact_by_id))

        # 3) coverage guard
        covered = {c.line_id for c in claims}
        for line in lyrics.all_lines():
            if line.line_id not in covered and is_medical_statement(line.text, topic):
                claims.append(
                    ClaimResult(
                        line_id=line.line_id,
                        claim=line.text,
                        status="ambiguous",
                        source_fact_id=None,
                        rationale="Line mentions the body/health but no claim was verified.",
                        checks=["uncovered_medical_line"],
                    )
                )

        for c in claims:
            c.severity = _SEVERITY.get(c.status)  # type: ignore[assignment]

        failing = sorted({c.line_id for c in claims if c.severity in ("critical", "major") and c.line_id})
        review = sorted({c.line_id for c in claims if c.severity == "minor" and c.line_id} - set(failing))
        if failing:
            status = "failed"
        elif review or not triage_ok:
            status = "requires_review"
        else:
            status = "passed"
        summary = {s: sum(c.status == s for c in claims)
                   for s in ("supported", "contradicted", "unsupported", "ambiguous", "outside_scope")}
        log_event(logger, "medical_validation", status=status, **summary)
        return MedicalAccuracyReport(
            status=status,
            claims=claims,
            failing_line_ids=failing,
            review_line_ids=review,
            summary=summary,
            automated_triage_available=triage_ok,
        )

    @staticmethod
    def _fallback_claims(lyrics: Lyrics, fact_by_id: dict[str, dict[str, str]]) -> list[ClaimResult]:
        out: list[ClaimResult] = []
        for line in lyrics.all_lines():
            for fid in line.fact_ids:
                if fid in fact_by_id and overlap_ratio(line.text, fact_by_id[fid]["text"]) >= FALLBACK_SUPPORT_OVERLAP:
                    out.append(ClaimResult(line_id=line.line_id, claim=line.text, status="supported",
                                           source_fact_id=fid, rationale="deterministic lexical match",
                                           checks=["fallback_lexical"]))
                    break
        return out
