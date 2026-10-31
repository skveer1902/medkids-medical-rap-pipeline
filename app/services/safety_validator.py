"""Stage 6 - child-safety validation.

Deterministic layer: categorised phrase/regex rules (not a bare keyword
blacklist: patterns look at word combinations, negation context and
age-band-specific complexity limits) plus structural checks.

LLM layer: a subjective review of tone and implied meaning. Its findings are
validated against the schema and line ids. If it is unavailable the report
can at best be ``requires_review``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.exceptions import LLMError
from app.core.logging import get_logger, log_event
from app.llm.base import LLMProvider
from app.models.schemas import LLMSafetyOutput, Lyrics, SafetyFinding, SafetyReport
from app.services.age_adapter import get_profile
from app.services.lexicon import ADVANCED_JARGON
from app.services.prompt_builder import build_safety_request
from app.utils.text_utils import estimate_syllables, words

logger = get_logger(__name__)

VALIDATOR_VERSION = "1.0.0"


@dataclass(frozen=True)
class Rule:
    category: str
    severity: str
    pattern: re.Pattern[str]
    explanation: str
    suggestion: str
    negatable: bool = False


def _r(p: str) -> re.Pattern[str]:
    return re.compile(p, re.IGNORECASE)


RULES: list[Rule] = [
    Rule("graphic_medical", "critical",
         _r(r"\b(gore|gory|guts|spurt\w*|gush\w*|squirt\w*|bleed(s|ing)?\s+out|blood\s*bath|bloody|mangled|"
            r"splatter\w*|severed|rip\w*\s+(it\s+|you\s+|them\s+)?(out|open|apart)|cut\s+(you\s+|it\s+|them\s+)?open|"
            r"chop\w*\s+(off|up))\b"),
         "Graphic or gory description of the body.", "Describe what the body does, not injuries."),
    Rule("frightening", "major",
         _r(r"\b(die|dies|dying|dead|death|kill\w*|murder\w*|scary|scared|terrif\w*|nightmares?|monsters?|"
            r"explod\w*|heart\s+attacks?|strokes?|cancer|tumou?rs?|coma|you\s+could\s+get\s+sick)\b"),
         "Language that could frighten a young child.", "Keep the tone safe and reassuring.", negatable=True),
    Rule("inappropriate_vocabulary", "major",
         _r(r"\b(damn|hell|crap|sucks?|stupid|dumb|idiots?|morons?|losers?|shut\s+up|butt|freaks?|ugly)\b"),
         "Rude or insulting vocabulary.", "Use kind, positive words."),
    Rule("unsafe_health_advice", "critical",
         _r(r"\b(take|swallow|eat|drink|try)\b.{0,25}\b(pills?|tablets?|medicines?|medications?|drugs?|"
            r"supplements?)\b"),
         "Advice about taking medicine.", "Remove medicine advice; children should ask a trusted adult."),
    Rule("unsafe_health_advice", "critical",
         _r(r"\b\d+\s?(mg|milligrams?|ml|millilit\w*|pills?|tablets?|doses?)\b"),
         "Contains a dose or quantity of medicine.", "Remove all dosing information."),
    Rule("unsafe_health_advice", "critical",
         _r(r"\b(don'?t|do\s+not|never|no\s+need\s+to)\s+(go\s+to|see|visit|tell|call|ask)\s+(the\s+|a\s+|your\s+)?"
            r"(doctors?|nurses?|dentists?|grown-?ups?|parents?|adults?)\b"),
         "Discourages seeking help from adults or health professionals.", "Encourage asking a trusted adult."),
    Rule("unsafe_health_advice", "critical",
         _r(r"\b(skip|stop)\s+(eating|meals?|breakfast|lunch|dinner|drinking\s+water|your\s+medicine)\b"),
         "Encourages skipping food, water or medicine.", "Remove this advice."),
    Rule("unsafe_health_advice", "major",
         _r(r"\bhold\s+your\s+breath\b.{0,25}\b(long|until|minutes?|forever)\b"),
         "Encourages a potentially unsafe breath-holding challenge.", "Remove the challenge."),
    Rule("unsafe_health_advice", "major",
         _r(r"\b(diets?|dieting|lose\s+weight|calories|skinny)\b"),
         "Diet/weight content is not appropriate for this audience.", "Remove diet and weight references."),
    Rule("misleading_health_claim", "major",
         _r(r"\b(cures?|never\s+(get|be)\s+sick|always\s+(be\s+)?healthy|100\s*%|guarantee\w*|detox\w*|miracle|"
            r"superfoods?|immune\s+to)\b"),
         "Absolute or misleading health promise.", "Avoid promises; describe what the body does."),
    Rule("stereotype", "major",
         _r(r"\b(boys|girls|men|women|ladies|guys)\s+(are|aren'?t|can'?t|cannot|don'?t|shouldn'?t|should)\b"),
         "Generalisation about a gender group.", "Make statements about everyone."),
    Rule("stereotype", "major", _r(r"\bonly\s+(boys|girls|men|women)\b|\blike\s+a\s+girl\b"),
         "Gender stereotype.", "Make statements about everyone."),
    Rule("stereotype", "major",
         _r(r"\b(doctors?|surgeons?|scientists?)\s+(are|is)\s+(always\s+)?(men|guys|boys)\b|"
            r"\bnurses?\s+(are|is)\s+(always\s+)?(women|girls|ladies)\b"),
         "Occupational gender stereotype.", "Anyone can be a doctor, nurse or scientist."),
    Rule("discriminatory", "critical",
         _r(r"\b(fat|skinny|chubby|disabled|crippled?|retard\w*|lame|weird)\s+(kids?|people|persons?|ones?|bodies)\b"),
         "Demeaning reference to a group of people.", "Remove; describe bodies respectfully."),
    Rule("stereotype", "minor", _r(r"\b(fat|chubby|obese)\b"),
         "Body-size reference; check for body shaming.", "Avoid body-size words."),
    Rule("copyright_imitation", "major", _r(r"\b(remix\s+of|sampled\s+from|in\s+the\s+style\s+of)\b"),
         "Possible imitation of an existing work.", "Keep lyrics fully original."),
]

_NEGATION = re.compile(r"(don'?t\s+be|no\s+need\s+to\s+be|not|never|nothing|no)\s+\w*\s*$", re.I)


def rule_findings(lyrics: Lyrics) -> list[SafetyFinding]:
    findings: list[SafetyFinding] = []
    for line in lyrics.all_lines():
        for rule in RULES:
            m = rule.pattern.search(line.text)
            if not m:
                continue
            severity = rule.severity
            explanation = rule.explanation
            if rule.negatable and _NEGATION.search(line.text[: m.start()]):
                severity = "minor"
                explanation += " (negated context; reviewer should confirm tone)"
            findings.append(
                SafetyFinding(line_id=line.line_id, category=rule.category, severity=severity,  # type: ignore[arg-type]
                              explanation=f"{explanation} Matched: '{m.group(0)}'.",
                              suggestion=rule.suggestion, source="rule")
            )
    return findings


def complexity_findings(lyrics: Lyrics, band: str, required_vocabulary: list[str]) -> list[SafetyFinding]:
    profile = get_profile(band)
    allowed = {w for term in required_vocabulary for w in words(term)}
    findings: list[SafetyFinding] = []
    for line in lyrics.all_lines():
        ws = words(line.text)
        if len(ws) > profile.max_words_per_line:
            severe = len(ws) > profile.max_words_per_line * 1.5
            findings.append(SafetyFinding(
                line_id=line.line_id, category="unnecessary_complexity", severity="major" if severe else "minor",
                explanation=f"{len(ws)} words; target is at most {profile.max_words_per_line} for ages {band}.",
                suggestion="Shorten the line.", source="rule"))
        long_words = [w for w in ws if estimate_syllables(w) >= 4 and w not in allowed]
        if len(long_words) > profile.max_long_words_per_line:
            findings.append(SafetyFinding(
                line_id=line.line_id, category="unnecessary_complexity", severity="minor",
                explanation=f"Several long words for ages {band}: {long_words}.",
                suggestion="Use shorter, familiar words.", source="rule"))
        jargon = sorted({w for w in ws if w in profile.flagged_jargon and w not in allowed})
        if jargon:
            severe = band == "4-6" and any(w in ADVANCED_JARGON for w in jargon)
            findings.append(SafetyFinding(
                line_id=line.line_id, category="unnecessary_complexity", severity="major" if severe else "minor",
                explanation=f"Technical terms not in the approved vocabulary for ages {band}: {jargon}.",
                suggestion="Replace with everyday words or add the term to the topic vocabulary with an explanation.",
                source="rule"))
    return findings


def structure_findings(lyrics: Lyrics) -> list[SafetyFinding]:
    findings: list[SafetyFinding] = []
    names = [s.section for s in lyrics.sections]
    for required in ("verse_1", "chorus", "verse_2"):
        if required not in names:
            findings.append(SafetyFinding(line_id=None, category="structure", severity="major",
                                          explanation=f"Missing required section '{required}'.", source="rule"))
    for line in lyrics.all_lines():
        if not line.text.strip():
            findings.append(SafetyFinding(line_id=line.line_id, category="structure", severity="major",
                                          explanation="Empty lyric line.", source="rule"))
    return findings


class SafetyValidator:
    def __init__(self, provider: LLMProvider, *, json_max_attempts: int = 3) -> None:
        self.provider = provider
        self.json_max_attempts = json_max_attempts

    def validate(self, *, topic: str, band: str, lyrics: Lyrics, required_vocabulary: list[str]) -> SafetyReport:
        findings = rule_findings(lyrics) + complexity_findings(lyrics, band, required_vocabulary) + structure_findings(lyrics)
        line_ids = set(lyrics.line_map())
        llm_ok = True
        try:
            out: LLMSafetyOutput = self.provider.generate_json(
                build_safety_request(topic=topic, band=band, lyrics=lyrics),
                LLMSafetyOutput,
                max_attempts=self.json_max_attempts,
            ).data
            for f in out.findings:
                lid = f.line_id if f.line_id in line_ids else None
                findings.append(SafetyFinding(line_id=lid, category=f.category, severity=f.severity,
                                              explanation=f.explanation, suggestion=f.suggestion, source="llm"))
        except LLMError as exc:
            llm_ok = False
            log_event(logger, "safety_llm_unavailable", error=exc.code)
            findings.append(SafetyFinding(
                line_id=None, category="other", severity="minor",
                explanation="Automated subjective safety review was unavailable; a human must review tone.",
                source="rule"))

        blocking = [f for f in findings if f.severity in ("critical", "major")]
        failing = sorted({f.line_id for f in blocking if f.line_id})
        if blocking:
            status = "failed"
        elif findings:
            status = "requires_review"
        else:
            status = "passed"
        log_event(logger, "safety_validation", status=status, findings=len(findings), blocking=len(blocking))
        return SafetyReport(status=status, findings=findings, failing_line_ids=failing, llm_review_available=llm_ok)
