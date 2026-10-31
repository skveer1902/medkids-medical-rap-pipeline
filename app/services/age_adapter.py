"""Stage 3 - age-based adaptation.

Two pilot age bands. The numeric limits below are design hypotheses from the
research proposal (not validated reading-level cut-offs) and should be
calibrated with educators and comprehension pilots.

Simplified wording is only a phrasing aid for the songwriter: medical
validation is always performed against the ORIGINAL approved fact text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.exceptions import InputRejectedError, LLMError
from app.core.logging import get_logger, log_event
from app.llm.base import LLMProvider
from app.models.schemas import AgeBand, LLMSimplificationOutput
from app.services.lexicon import ABSOLUTE_WORDS, ADVANCED_JARGON, INTERMEDIATE_JARGON, MEDICAL_TERMS
from app.utils.text_utils import content_words, stem, words

logger = get_logger(__name__)


@dataclass(frozen=True)
class AgeProfile:
    band: AgeBand
    max_words_per_line: int
    max_lines_per_section: int
    max_long_words_per_line: int  # words with >= 3 syllables
    guidelines: tuple[str, ...]
    flagged_jargon: frozenset[str] = field(default_factory=frozenset)

    def guidelines_text(self) -> str:
        return "\n".join(f"- {g}" for g in self.guidelines)

    def short_text(self) -> str:
        return (
            f"at most {self.max_words_per_line} words per line, simple words, "
            f"no unexplained science terms"
        )


AGE_PROFILES: dict[str, AgeProfile] = {
    "4-6": AgeProfile(
        band="4-6",
        max_words_per_line=9,
        max_lines_per_section=8,
        max_long_words_per_line=1,
        guidelines=(
            "Very short lines (about 4-9 words) using familiar, everyday words.",
            "Focus on a few concrete ideas; repeat key ideas and the chorus often.",
            "Use action verbs children know (pump, push, think, move, jump).",
            "Call-and-response is welcome (e.g. 'Where's your heart? Right here!').",
            "Avoid science terms; if one is essential, explain it immediately in simple words.",
            "Warm, happy, encouraging tone. Nothing scary.",
        ),
        flagged_jargon=ADVANCED_JARGON | INTERMEDIATE_JARGON,
    ),
    "7-10": AgeProfile(
        band="7-10",
        max_words_per_line=13,
        max_lines_per_section=12,
        max_long_words_per_line=2,
        guidelines=(
            "Lines of about 6-13 words.",
            "Introduce selected science words (from the vocabulary list) and explain each right away.",
            "Simple cause-and-effect is welcome (X happens, so Y happens).",
            "Keep repetition in the chorus to support memory.",
            "Positive, curious tone. Nothing scary.",
        ),
        flagged_jargon=ADVANCED_JARGON,
    ),
}


def resolve_age_band(topic_min: int, topic_max: int, requested: AgeBand | None) -> AgeBand:
    """Pick the age band for a generation request.

    If the request names a band, it must overlap the topic's age range. If not,
    the band is derived from the topic; a topic spanning both bands defaults to
    the younger (more conservative) band.
    """
    bands: dict[AgeBand, tuple[int, int]] = {"4-6": (4, 6), "7-10": (7, 10)}
    if requested is not None:
        lo, hi = bands[requested]
        if hi < topic_min or lo > topic_max:
            raise InputRejectedError(
                f"age_band {requested} does not overlap the topic's target ages {topic_min}-{topic_max}"
            )
        return requested
    return "4-6" if topic_min <= 6 else "7-10"


def get_profile(band: str) -> AgeProfile:
    return AGE_PROFILES[band]


_NUM_RE = re.compile(r"\d+")


def check_meaning_preserved(
    original: str, simplified: str, forbidden_patterns: list[re.Pattern[str]]
) -> list[str]:
    """Deterministic guard: return reasons why ``simplified`` may have changed meaning."""
    problems: list[str] = []
    orig_words = {stem(w) for w in words(original)}
    simp_words = {stem(w) for w in words(simplified)}
    new_abs = {w for w in words(simplified) if w in ABSOLUTE_WORDS} - set(words(original))
    if new_abs:
        problems.append(f"adds absolute wording: {sorted(new_abs)}")
    new_numbers = set(_NUM_RE.findall(simplified)) - set(_NUM_RE.findall(original))
    if new_numbers:
        problems.append(f"adds numbers: {sorted(new_numbers)}")
    new_medical = {w for w in simp_words if w in MEDICAL_TERMS} - orig_words
    # Allow very generic words the simplifier commonly uses.
    new_medical -= {stem(w) for w in ("body", "move", "think", "feel", "air", "carry", "travel")}
    if new_medical:
        problems.append(f"introduces medical terms not in the fact: {sorted(new_medical)}")
    if not (content_words(original) & content_words(simplified)):
        problems.append("shares no key words with the approved fact")
    for pat in forbidden_patterns:
        if pat.search(simplified):
            problems.append("matches a forbidden claim")
    return problems


class AgeAdapter:
    def __init__(self, provider: LLMProvider, *, json_max_attempts: int = 3) -> None:
        self.provider = provider
        self.json_max_attempts = json_max_attempts

    def simplify_facts(
        self,
        *,
        topic: str,
        band: AgeBand,
        facts: list[dict[str, str]],
        objectives: list[dict[str, str]],
        forbidden_patterns: list[re.Pattern[str]],
    ) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        """Return ``(facts_with_simplified_text, notes)``.

        Falls back to the original approved wording for any fact whose
        simplification fails the meaning-preservation check, and for all facts
        if the LLM call itself fails (approved originals are always safe to use).
        """
        from app.services.prompt_builder import build_simplification_request

        notes: list[dict[str, str]] = []
        result = [{**f, "simplified_text": f["text"]} for f in facts]
        if not facts:
            return result, notes
        request = build_simplification_request(
            topic=topic, band=band, facts=facts, objectives=objectives
        )
        try:
            out = self.provider.generate_json(
                request, LLMSimplificationOutput, max_attempts=self.json_max_attempts
            )
        except LLMError as exc:
            log_event(logger, "simplification_fallback", error=exc.code)
            notes.append({"fact_id": "*", "note": f"simplification unavailable ({exc.code}); using approved wording"})
            return result, notes

        by_id = {item.fact_id: item.simplified_text for item in out.data.items}
        for f in result:
            simplified = by_id.get(f["fact_id"])
            if simplified is None:
                notes.append({"fact_id": f["fact_id"], "note": "not returned; using approved wording"})
                continue
            problems = check_meaning_preserved(f["text"], simplified, forbidden_patterns)
            if problems:
                notes.append({"fact_id": f["fact_id"], "note": "rejected simplification: " + "; ".join(problems)})
                continue
            f["simplified_text"] = simplified
        return result, notes
