"""Stage 8 - targeted revision.

Only lines that failed validation (or that a reviewer flagged) are sent for
rewriting. Preservation of every other line is ENFORCED in code: replacement
lines whose id is not in the target set are discarded, so a model cannot
"helpfully" rewrite approved sections. The caller enforces the attempt cap.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.llm.base import LLMProvider
from app.models.schemas import (
    LLMRevisionOutput,
    LyricLine,
    Lyrics,
    LyricSection,
    MedicalAccuracyReport,
    SafetyReport,
)
from app.services.prompt_builder import build_revision_request, load_prompt


def collect_problems(
    medical: MedicalAccuracyReport | None,
    safety: SafetyReport | None,
    reviewer_issues: list[dict] | None = None,
) -> dict[str, list[str]]:
    problems: dict[str, list[str]] = {}

    def add(line_id: str | None, reason: str) -> None:
        if line_id:
            problems.setdefault(line_id, []).append(reason)

    if medical:
        for c in medical.claims:
            if c.severity in ("critical", "major"):
                add(c.line_id, f"medical: {c.status} - {c.rationale or c.claim}")
    if safety:
        for f in safety.findings:
            if f.severity in ("critical", "major"):
                add(f.line_id, f"safety/{f.category}: {f.explanation}"
                    + (f" Suggestion: {f.suggestion}" if f.suggestion else ""))
    for issue in reviewer_issues or []:
        add(issue.get("line_id"), f"reviewer ({issue.get('severity', 'major')}): {issue.get('reason', '')}")
    return problems


@dataclass
class RevisionResult:
    lyrics: Lyrics
    changed_line_ids: list[str]
    ignored_line_ids: list[str]
    prompt_name: str
    prompt_version: str
    prompt_sha256: str
    model: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float | None
    notes: str | None


def apply_revisions(lyrics: Lyrics, revised: dict[str, LyricLine], targets: set[str]) -> tuple[Lyrics, list[str]]:
    changed: list[str] = []
    sections = []
    for s in lyrics.sections:
        lines = []
        for ln in s.lines:
            new = revised.get(ln.line_id)
            if ln.line_id in targets and new is not None and new.text.strip() and new.text.strip() != ln.text:
                lines.append(LyricLine(line_id=ln.line_id, text=new.text.strip(), fact_ids=new.fact_ids))
                changed.append(ln.line_id)
            else:
                lines.append(ln.model_copy(deep=True))
        sections.append(LyricSection(section=s.section, lines=lines))
    return Lyrics(title=lyrics.title, sections=sections, arrangement=list(lyrics.arrangement)), changed


class RevisionService:
    def __init__(self, provider: LLMProvider, *, temperature: float | None, json_max_attempts: int = 3) -> None:
        self.provider = provider
        self.temperature = temperature if provider.supports_temperature else None
        self.json_max_attempts = json_max_attempts

    def revise(
        self,
        *,
        topic: str,
        band: str,
        lyrics: Lyrics,
        facts: list[dict[str, str]],
        forbidden_claims: list[str],
        problems: dict[str, list[str]],
    ) -> RevisionResult:
        targets = {lid for lid in problems if lid in lyrics.line_map()}
        request = build_revision_request(
            topic=topic, band=band, lyrics=lyrics, facts=facts, forbidden_claims=forbidden_claims,
            problems={k: v for k, v in problems.items() if k in targets}, temperature=self.temperature,
        )
        result = self.provider.generate_json(request, LLMRevisionOutput, max_attempts=self.json_max_attempts)
        out: LLMRevisionOutput = result.data
        approved_ids = {f["fact_id"] for f in facts}
        revised = {
            r.line_id: LyricLine(line_id=r.line_id, text=r.text,
                                 fact_ids=[fid for fid in r.fact_ids if fid in approved_ids])
            for r in out.revised_lines
        }
        ignored = sorted(set(revised) - targets)
        new_lyrics, changed = apply_revisions(lyrics, revised, targets)
        template = load_prompt("lyric_revision")
        return RevisionResult(
            lyrics=new_lyrics,
            changed_line_ids=changed,
            ignored_line_ids=ignored,
            prompt_name=template.name,
            prompt_version=template.version,
            prompt_sha256=template.sha256,
            model=result.response.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            estimated_cost_usd=result.estimated_cost_usd,
            notes=out.notes,
        )
