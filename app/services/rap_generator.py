"""Stage 4 - controlled rap generation.

The LLM output is (1) parsed and validated against :class:`LLMRapOutput`,
(2) checked structurally against the request (required sections, chorus
length), and (3) normalised: deterministic line ids are assigned and any
fact id the model cites that is not in the approved set is dropped. Failures
are retried a bounded number of times with the error fed back to the model.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.core.exceptions import LLMOutputValidationError
from app.core.logging import get_logger, log_event
from app.llm.base import LLMProvider
from app.models.schemas import GenerationSpec, LLMRapOutput, LyricLine, Lyrics, LyricSection
from app.services.prompt_builder import build_arrangement, build_generation_request, load_prompt

logger = get_logger(__name__)

_SECTION_PREFIX = {"intro": "in", "chorus": "ch", "outro": "out"}


def section_prefix(section: str) -> str:
    if section.startswith("verse_"):
        return "v" + section.split("_", 1)[1]
    return _SECTION_PREFIX[section]


def canonical_order(sections: list[str]) -> list[str]:
    def key(s: str) -> tuple[int, int]:
        if s == "intro":
            return (0, 0)
        if s.startswith("verse_"):
            n = int(s.split("_")[1])
            return (1, 0) if n == 1 else (3, n)
        if s == "chorus":
            return (2, 0)
        return (9, 0)

    return sorted(sections, key=key)


@dataclass
class GeneratedRap:
    lyrics: Lyrics
    objectives_covered: list[str]
    provider: str
    model: str
    prompt_name: str
    prompt_version: str
    prompt_sha256: str
    generation_config: dict[str, Any]
    run_id: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float | None
    notes: list[str] = field(default_factory=list)


def structural_problems(out: LLMRapOutput, spec: GenerationSpec) -> list[str]:
    problems: list[str] = []
    names = [s.section for s in out.sections]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        problems.append(f"duplicate sections {sorted(dupes)}; write the chorus once")
    required = [f"verse_{n}" for n in range(1, spec.num_verses + 1)] + ["chorus"]
    missing = [r for r in required if r not in names]
    if missing:
        problems.append(f"missing required sections {missing}")
    extra_verses = [n for n in names if n.startswith("verse_") and int(n.split("_")[1]) > spec.num_verses]
    if extra_verses:
        problems.append(f"too many verses {extra_verses}; requested {spec.num_verses}")
    if not spec.include_intro and "intro" in names:
        problems.append("intro was not requested")
    if not spec.include_outro and "outro" in names:
        problems.append("outro was not requested")
    for s in out.sections:
        if s.section == "chorus" and len(s.lines) > spec.chorus.max_lines:
            problems.append(f"chorus has {len(s.lines)} lines; max is {spec.chorus.max_lines}")
    if spec.chorus.include_topic_name:
        chorus = next((s for s in out.sections if s.section == "chorus"), None)
        if chorus and not any(spec.topic_title.lower() in ln.text.lower() for ln in chorus.lines):
            problems.append(f"chorus must mention '{spec.topic_title}'")
    return problems


def to_lyrics(out: LLMRapOutput, spec: GenerationSpec, approved_ids: set[str]) -> tuple[Lyrics, list[str]]:
    notes: list[str] = []
    by_name = {s.section: s for s in out.sections}
    sections: list[LyricSection] = []
    for name in canonical_order(list(by_name)):
        prefix = section_prefix(name)
        lines = []
        for i, ln in enumerate(by_name[name].lines, start=1):
            ids = [fid for fid in ln.fact_ids if fid in approved_ids]
            dropped = [fid for fid in ln.fact_ids if fid not in approved_ids]
            if dropped:
                notes.append(f"{prefix}_l{i:02d}: dropped unapproved fact ids {dropped}")
            lines.append(LyricLine(line_id=f"{prefix}_l{i:02d}", text=ln.text.strip(), fact_ids=ids))
        sections.append(LyricSection(section=name, lines=lines))
    return Lyrics(title=out.title.strip(), sections=sections, arrangement=build_arrangement(spec)), notes


class RapGenerator:
    def __init__(
        self,
        provider: LLMProvider,
        *,
        temperature: float | None,
        max_tokens: int,
        json_max_attempts: int = 3,
        extra_config: dict[str, Any] | None = None,
    ) -> None:
        self.provider = provider
        self.temperature = temperature if provider.supports_temperature else None
        self.requested_temperature = temperature
        self.max_tokens = max_tokens
        self.json_max_attempts = json_max_attempts
        self.extra_config = extra_config or {}

    def generate(self, spec: GenerationSpec) -> GeneratedRap:
        if not spec.approved_facts:
            raise LLMOutputValidationError("cannot generate without approved facts")
        run_id = uuid.uuid4().hex
        request = build_generation_request(spec, temperature=self.temperature, max_tokens=self.max_tokens)
        template = load_prompt("rap_generation")
        approved_ids = {f["fact_id"] for f in spec.approved_facts}
        objective_ids = {o["objective_id"] for o in spec.learning_objectives}
        base_prompt = request.prompt
        in_tok = out_tok = 0
        cost: float | None = None
        last_error = ""
        for attempt in range(1, self.json_max_attempts + 1):
            if attempt > 1:
                request.prompt = (
                    f"{base_prompt}\n\n<previous_attempt_error>\n{last_error[:600]}\n"
                    "Fix these problems and return ONLY the JSON object.\n</previous_attempt_error>"
                )
            try:
                result = self.provider.generate_json(request, LLMRapOutput, max_attempts=1)
            except LLMOutputValidationError as exc:
                # usage of failed calls is still recorded by the provider's usage tracker
                last_error = str(exc.details.get("last_error") or exc.message)
                continue
            in_tok += result.input_tokens
            out_tok += result.output_tokens
            if result.estimated_cost_usd is not None:
                cost = (cost or 0.0) + result.estimated_cost_usd
            out: LLMRapOutput = result.data
            problems = structural_problems(out, spec)
            if problems:
                last_error = "Structure problems: " + "; ".join(problems)
                log_event(logger, "generation_structure_invalid", run_id=run_id, attempt=attempt,
                          problems=len(problems))
                continue
            try:
                lyrics, notes = to_lyrics(out, spec, approved_ids)
            except (ValidationError, KeyError, ValueError) as exc:
                last_error = f"Could not normalise lyrics: {exc}"
                continue
            covered = [o for o in out.objectives_covered if o in objective_ids]
            log_event(logger, "rap_generated", run_id=run_id, attempts=attempt,
                      lines=len(lyrics.all_lines()), provider=self.provider.name)
            return GeneratedRap(
                lyrics=lyrics,
                objectives_covered=covered,
                provider=self.provider.name,
                model=result.response.model,
                prompt_name=template.name,
                prompt_version=template.version,
                prompt_sha256=template.sha256,
                generation_config={
                    "temperature": self.temperature
                    if self.temperature is not None
                    else ("not_supported_by_provider" if self.requested_temperature is not None else None),
                    "max_tokens": self.max_tokens,
                    "json_attempts_used": attempt,
                    "style": spec.style,
                    "num_verses": spec.num_verses,
                    "chorus": spec.chorus.model_dump(),
                    "include_intro": spec.include_intro,
                    "include_outro": spec.include_outro,
                    **self.extra_config,
                },
                run_id=run_id,
                input_tokens=in_tok,
                output_tokens=out_tok,
                estimated_cost_usd=cost,
                notes=notes,
            )
        raise LLMOutputValidationError(
            f"Rap generation failed validation after {self.json_max_attempts} attempts: {last_error[:300]}",
            details={"run_id": run_id},
        )
