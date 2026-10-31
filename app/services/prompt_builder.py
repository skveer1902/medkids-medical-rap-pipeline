"""Versioned prompt templates and request construction.

Templates live in ``app/prompts/*.txt`` with a small header::

    # name: rap_generation
    # version: 1.0.0
    === SYSTEM ===
    ...
    === USER ===
    ...

Placeholders use ``{{name}}``. Rendering fails loudly on missing or unknown
placeholders. Each template's SHA-256 is stored with every generated version
so experiments are reproducible. All untrusted text (source documents,
lyrics, user-entered topic content) is wrapped in ``<untrusted_*>`` tags with
delimiter-escaping so it cannot close the tag and masquerade as instructions.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from app.llm.base import LLMRequest
from app.models.schemas import GenerationSpec, Lyrics
from app.services.age_adapter import get_profile
from app.utils.text_utils import sha256, wrap_untrusted

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    version: str
    system: str
    user: str
    sha256: str

    def placeholders(self) -> set[str]:
        return set(_PLACEHOLDER_RE.findall(self.system)) | set(_PLACEHOLDER_RE.findall(self.user))

    def render(self, **values: Any) -> tuple[str, str]:
        needed = self.placeholders()
        missing = needed - values.keys()
        if missing:
            raise KeyError(f"prompt '{self.name}' missing values: {sorted(missing)}")
        unknown = values.keys() - needed
        if unknown:
            raise KeyError(f"prompt '{self.name}' got unknown values: {sorted(unknown)}")

        def sub(text: str) -> str:
            return _PLACEHOLDER_RE.sub(lambda m: str(values[m.group(1)]), text)

        return sub(self.system), sub(self.user)


@cache
def load_prompt(name: str) -> PromptTemplate:
    path = PROMPTS_DIR / f"{name}.txt"
    raw = path.read_text(encoding="utf-8")
    header: dict[str, str] = {}
    lines = raw.splitlines()
    i = 0
    while i < len(lines) and lines[i].startswith("#"):
        key, _, value = lines[i][1:].partition(":")
        header[key.strip()] = value.strip()
        i += 1
    body = "\n".join(lines[i:])
    if "=== SYSTEM ===" not in body or "=== USER ===" not in body:
        raise ValueError(f"prompt {name} must contain SYSTEM and USER sections")
    system = body.split("=== SYSTEM ===", 1)[1].split("=== USER ===", 1)[0].strip()
    user = body.split("=== USER ===", 1)[1].strip()
    if header.get("name") != name or not header.get("version"):
        raise ValueError(f"prompt {name} header must declare matching name and a version")
    return PromptTemplate(name=name, version=header["version"], system=system, user=user, sha256=sha256(raw))


def list_prompt_versions() -> dict[str, dict[str, str]]:
    out = {}
    for path in sorted(PROMPTS_DIR.glob("*.txt")):
        t = load_prompt(path.stem)
        out[t.name] = {"version": t.version, "sha256": t.sha256}
    return out


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items) if items else "- (none)"


def format_objectives(objectives: list[dict[str, str]]) -> str:
    return "\n".join(
        wrap_untrusted("untrusted_objective", o["text"], id=o["objective_id"]) for o in objectives
    ) or "(none)"


def format_facts(facts: list[dict[str, str]], *, simplified: bool = False) -> str:
    rows = []
    for f in facts:
        text = f["text"]
        if simplified and f.get("simplified_text") and f["simplified_text"] != f["text"]:
            text = f"{f['text']}\n(child-friendly wording: {f['simplified_text']})"
        rows.append(wrap_untrusted("untrusted_fact", text, id=f["fact_id"]))
    return "\n".join(rows) or "(no approved facts)"


def format_forbidden(claims: list[str]) -> str:
    return "\n".join(wrap_untrusted("untrusted_forbidden_claim", c) for c in claims) or "(none)"


def format_lyrics(lyrics: Lyrics) -> str:
    rows = [f"[{s.section}]\n" + "\n".join(f"{ln.line_id}: {ln.text}" for ln in s.lines) for s in lyrics.sections]
    return wrap_untrusted("untrusted_lyrics", "\n".join(rows))


def _request(template: PromptTemplate, task: str, values: dict[str, Any], *, max_tokens: int,
             temperature: float | None, context: dict[str, Any]) -> LLMRequest:
    system, user = template.render(**values)
    return LLMRequest(
        task=task,
        system=system,
        prompt=user,
        temperature=temperature,
        max_tokens=max_tokens,
        prompt_name=template.name,
        prompt_version=template.version,
        context=context,
    )


# --------------------------------------------------------------------------
# Stage request builders
# --------------------------------------------------------------------------


def build_fact_extraction_request(
    *,
    topic: str,
    age_min: int,
    age_max: int,
    sources: list[dict[str, str]],
    objectives: list[dict[str, str]],
    forbidden_claims: list[str],
    max_facts: int = 15,
    max_tokens: int = 2000,
) -> LLMRequest:
    t = load_prompt("fact_extraction")
    src = "\n\n".join(
        wrap_untrusted("untrusted_source_document", s["content"], id=s["source_id"], version=s["version"])
        for s in sources
    )
    values = {
        "topic": topic,
        "age_min": age_min,
        "age_max": age_max,
        "objectives": format_objectives(objectives),
        "forbidden_claims": format_forbidden(forbidden_claims),
        "sources": src,
        "max_facts": max_facts,
    }
    return _request(t, "fact_extraction", values, max_tokens=max_tokens, temperature=0.0,
                    context={"sources": sources, "max_facts": max_facts, "forbidden_claims": forbidden_claims})


def build_simplification_request(
    *, topic: str, band: str, facts: list[dict[str, str]], objectives: list[dict[str, str]], max_tokens: int = 1500
) -> LLMRequest:
    t = load_prompt("age_simplification")
    profile = get_profile(band)
    values = {
        "topic": topic,
        "age_band": band,
        "age_guidelines": profile.guidelines_text(),
        "objectives": format_objectives(objectives),
        "facts": format_facts(facts),
    }
    return _request(t, "age_simplification", values, max_tokens=max_tokens, temperature=0.2,
                    context={"band": band, "facts": facts})


def build_arrangement(spec: GenerationSpec) -> list[str]:
    """Performance order. The chorus is written once and repeated here."""
    order: list[str] = ["intro"] if spec.include_intro else []
    for n in range(1, spec.num_verses + 1):
        order.append(f"verse_{n}")
        if n <= spec.chorus.repeat_times or n == spec.num_verses:
            order.append("chorus")
    if spec.include_outro:
        order.append("outro")
    return order


def describe_structure(spec: GenerationSpec) -> str:
    profile = get_profile(spec.age_band)
    parts = []
    if spec.include_intro:
        parts.append("- intro: 2-4 lines (warm-up; facts optional)")
    for n in range(1, spec.num_verses + 1):
        parts.append(f"- verse_{n}: 4-{profile.max_lines_per_section} lines")
    chorus = f"- chorus: 2-{spec.chorus.max_lines} lines, written ONCE (it is repeated when performed)"
    if spec.chorus.include_topic_name:
        chorus += f"; must mention '{spec.topic_title}'"
    parts.append(chorus + "; make it catchy and easy to sing back")
    if spec.include_outro:
        parts.append("- outro: 2-4 lines")
    parts.append(f"Performance order: {', '.join(build_arrangement(spec))}.")
    parts.append(
        f"Max {profile.max_words_per_line} words per line. Use rhyming couplets (AABB) where possible."
    )
    return "\n".join(parts)


def build_generation_request(spec: GenerationSpec, *, temperature: float | None, max_tokens: int) -> LLMRequest:
    t = load_prompt("rap_generation")
    profile = get_profile(spec.age_band)
    values = {
        "topic": spec.topic_title,
        "age_band": spec.age_band,
        "age_guidelines": profile.guidelines_text(),
        "structure": describe_structure(spec),
        "style": spec.style.replace("_", " "),
        "objectives": format_objectives(spec.learning_objectives),
        "facts": format_facts(spec.approved_facts, simplified=True),
        "forbidden_claims": format_forbidden(spec.forbidden_claims),
        "vocabulary": ", ".join(spec.required_vocabulary) or "(none)",
    }
    return _request(t, "rap_generation", values, max_tokens=max_tokens, temperature=temperature,
                    context={"spec": spec.model_dump()})


def build_medical_validation_request(
    *, topic: str, band: str, lyrics: Lyrics, facts: list[dict[str, str]], forbidden_claims: list[str],
    max_tokens: int = 3000,
) -> LLMRequest:
    t = load_prompt("medical_validation")
    values = {
        "topic": topic,
        "age_band": band,
        "facts": format_facts(facts),
        "forbidden_claims": format_forbidden(forbidden_claims),
        "lyrics": format_lyrics(lyrics),
    }
    return _request(t, "medical_validation", values, max_tokens=max_tokens, temperature=0.0,
                    context={"lyrics": lyrics.model_dump(), "facts": facts, "topic": topic})


def build_safety_request(*, topic: str, band: str, lyrics: Lyrics, max_tokens: int = 2000) -> LLMRequest:
    t = load_prompt("safety_validation")
    values = {"topic": topic, "age_band": band, "lyrics": format_lyrics(lyrics)}
    return _request(t, "safety_validation", values, max_tokens=max_tokens, temperature=0.0,
                    context={"lyrics": lyrics.model_dump()})


def build_revision_request(
    *, topic: str, band: str, lyrics: Lyrics, facts: list[dict[str, str]], forbidden_claims: list[str],
    problems: dict[str, list[str]], temperature: float | None, max_tokens: int = 2000,
) -> LLMRequest:
    t = load_prompt("lyric_revision")
    profile = get_profile(band)
    line_map = lyrics.line_map()
    problem_text = "\n".join(
        wrap_untrusted(
            "untrusted_problem",
            f"current: {line_map[lid].text}\nproblems: " + "; ".join(reasons),
            line_id=lid,
        )
        for lid, reasons in problems.items()
        if lid in line_map
    )
    values = {
        "topic": topic,
        "age_band": band,
        "age_guidelines_short": profile.short_text(),
        "facts": format_facts(facts, simplified=True),
        "forbidden_claims": format_forbidden(forbidden_claims),
        "lyrics": format_lyrics(lyrics),
        "problems": problem_text,
    }
    return _request(t, "lyric_revision", values, max_tokens=max_tokens, temperature=temperature,
                    context={"lyrics": lyrics.model_dump(), "facts": facts, "problems": problems})


def prompt_fingerprint(request: LLMRequest) -> str:
    return sha256(json.dumps({"s": request.system, "p": request.prompt}, sort_keys=True))
