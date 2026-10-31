"""Stage 7 - quality scoring (experimental pilot rubric).

Weighted soft scores (configurable via SCORE_WEIGHTS / SOFT_SCORE_PASS_THRESHOLD):
medical accuracy 30%, educational coverage 25%, age appropriateness 20%,
rhyme & rhythm 15%, memorability 10%.

Hard gates are evaluated separately and can never be outweighed by a score:
medical validation not failed, safety validation not failed, required
structure present, and at least one approved fact cited. Passing all of them
only makes a rap eligible for HUMAN review.

The rhyme/syllable heuristics are deliberately simple; the research notes
that such heuristics perform poorly on short rhythmic lyrics, so they must be
calibrated against human ratings before being trusted.
"""

from __future__ import annotations

import statistics

from app.models.schemas import (
    CriterionScore,
    EvaluationReport,
    Lyrics,
    MedicalAccuracyReport,
    SafetyReport,
)
from app.services.age_adapter import get_profile
from app.utils.text_utils import content_words, last_word, line_syllables, words, words_rhyme

RUBRIC_VERSION = "pilot-1.0"
COVERAGE_MATCH_MIN = 0.6


def score_medical(report: MedicalAccuracyReport) -> CriterionScore:
    claims = report.claims
    if not claims:
        return CriterionScore(score=50.0, weight=0, explanation="No factual claims detected; nothing verified.")
    supported = sum(c.status == "supported" for c in claims)
    contradicted = sum(c.status == "contradicted" for c in claims)
    unsupported = sum(c.status == "unsupported" for c in claims)
    if contradicted:
        return CriterionScore(score=0.0, weight=0, explanation=f"{contradicted} contradicted claim(s).")
    score = max(0.0, 100.0 * supported / len(claims) - 15.0 * unsupported)
    return CriterionScore(
        score=round(score, 1), weight=0,
        explanation=f"{supported}/{len(claims)} claims supported by approved facts; {unsupported} unsupported.",
    )


def objective_coverage(lyrics: Lyrics, objectives: list[dict[str, str]]) -> dict[str, bool]:
    lines = [ln.text for ln in lyrics.all_lines()]
    windows = lines + [f"{a} {b}" for a, b in zip(lines, lines[1:], strict=False)]
    covered: dict[str, bool] = {}
    for o in objectives:
        ow = content_words(o["text"])
        best = max((len(ow & content_words(w)) / len(ow) for w in windows), default=0.0) if ow else 0.0
        covered[o["objective_id"]] = best >= COVERAGE_MATCH_MIN
    return covered


def score_coverage(lyrics: Lyrics, objectives: list[dict[str, str]], declared: list[str]) -> CriterionScore:
    if not objectives:
        return CriterionScore(score=0.0, weight=0, explanation="Topic has no learning objectives.")
    cov = objective_coverage(lyrics, objectives)
    n = sum(cov.values())
    unverified = [o for o in declared if not cov.get(o, False)]
    expl = f"{n}/{len(objectives)} learning objectives verifiably covered."
    if unverified:
        expl += f" Model claimed coverage of {unverified} but it was not found in the lyrics."
    return CriterionScore(score=round(100.0 * n / len(objectives), 1), weight=0, explanation=expl)


def score_age(lyrics: Lyrics, band: str, safety: SafetyReport) -> CriterionScore:
    profile = get_profile(band)
    lines = lyrics.all_lines()
    lengths = [len(words(ln.text)) for ln in lines]
    over = sum(1 for n in lengths if n > profile.max_words_per_line)
    complexity = [f for f in safety.findings if f.category == "unnecessary_complexity"]
    major = sum(f.severity == "major" for f in complexity)
    score = 100.0 - 8 * over - 4 * (len(complexity) - major) - 15 * major
    avg = statistics.mean(lengths) if lengths else 0
    return CriterionScore(
        score=round(max(0.0, score), 1), weight=0,
        explanation=(f"Average {avg:.1f} words/line (max {profile.max_words_per_line} for ages {band}); "
                     f"{over} long line(s); {len(complexity)} complexity finding(s)."),
    )


def score_rhyme_rhythm(lyrics: Lyrics) -> CriterionScore:
    rhymed = total = 0
    cvs: list[float] = []
    for s in lyrics.sections:
        ends = [last_word(ln.text) for ln in s.lines]
        for i, w in enumerate(ends):
            total += 1
            neighbours = [ends[j] for j in (i - 2, i - 1, i + 1, i + 2) if 0 <= j < len(ends) and j != i]
            if any(words_rhyme(w, n) for n in neighbours):
                rhymed += 1
        syl = [line_syllables(ln.text) for ln in s.lines]
        if len(syl) >= 2 and statistics.mean(syl) > 0:
            cvs.append(statistics.pstdev(syl) / statistics.mean(syl))
    rhyme = rhymed / total if total else 0.0
    rhythm = max(0.0, 1.0 - (statistics.mean(cvs) if cvs else 1.0))
    score = 100.0 * (0.7 * rhyme + 0.3 * rhythm)
    return CriterionScore(
        score=round(score, 1), weight=0,
        explanation=(f"{rhymed}/{total} lines rhyme with a neighbour; syllable consistency {rhythm:.2f} "
                     "(heuristic, uncalibrated)."),
    )


def score_memorability(lyrics: Lyrics, topic: str) -> CriterionScore:
    chorus = next((s for s in lyrics.sections if s.section == "chorus"), None)
    if chorus is None:
        return CriterionScore(score=0.0, weight=0, explanation="No chorus.")
    pts = 20.0
    notes = ["chorus present"]
    if lyrics.arrangement.count("chorus") >= 2:
        pts += 25
        notes.append("chorus repeats")
    if any(topic.lower() in ln.text.lower() for ln in chorus.lines):
        pts += 20
        notes.append("topic named in chorus")
    if 2 <= len(chorus.lines) <= 4:
        pts += 15
        notes.append("short chorus")
    chorus_words = [w for ln in chorus.lines for w in words(ln.text) if len(w) > 2]
    if len(chorus_words) != len(set(chorus_words)):
        pts += 20
        notes.append("internal repetition / hook")
    return CriterionScore(score=pts, weight=0, explanation=", ".join(notes) + ".")


def recommendations(scores: dict[str, CriterionScore], medical: MedicalAccuracyReport,
                    safety: SafetyReport, coverage: dict[str, bool]) -> list[str]:
    recs: list[str] = []
    if medical.failing_line_ids:
        recs.append(f"Fix medically inaccurate/unsupported lines: {medical.failing_line_ids}.")
    if medical.review_line_ids:
        recs.append(f"Medical reviewer should check ambiguous lines: {medical.review_line_ids}.")
    if safety.failing_line_ids:
        recs.append(f"Fix child-safety issues on lines: {safety.failing_line_ids}.")
    missing = [k for k, v in coverage.items() if not v]
    if missing:
        recs.append(f"Add lines covering objectives: {missing}.")
    if scores["age_appropriateness"].score < 80:
        recs.append("Shorten long lines and replace technical words.")
    if scores["rhyme_rhythm"].score < 60:
        recs.append("Strengthen end rhymes (AABB couplets) and even out line lengths.")
    if scores["memorability"].score < 70:
        recs.append("Make the chorus shorter, repeat it, and include the topic name as a hook.")
    return recs


def evaluate(
    *,
    lyrics: Lyrics,
    topic: str,
    band: str,
    objectives: list[dict[str, str]],
    declared_objectives: list[str],
    medical: MedicalAccuracyReport,
    safety: SafetyReport,
    num_verses: int,
    weights: dict[str, float],
    threshold: float,
) -> EvaluationReport:
    scores = {
        "medical_accuracy": score_medical(medical),
        "educational_coverage": score_coverage(lyrics, objectives, declared_objectives),
        "age_appropriateness": score_age(lyrics, band, safety),
        "rhyme_rhythm": score_rhyme_rhythm(lyrics),
        "memorability": score_memorability(lyrics, topic),
    }
    for k, s in scores.items():
        s.weight = weights[k]
    total = round(sum(s.score * s.weight for s in scores.values()), 1)

    names = {s.section for s in lyrics.sections}
    required = {f"verse_{n}" for n in range(1, num_verses + 1)} | {"chorus"}
    gates = {
        "medical_accuracy_not_failed": medical.status != "failed",
        "child_safety_not_failed": safety.status != "failed",
        "required_structure_present": required <= names,
        "cites_approved_facts": any(ln.fact_ids for ln in lyrics.all_lines()),
    }
    soft_ok = total >= threshold
    return EvaluationReport(
        total_score=total,
        scores=scores,
        hard_gates=gates,
        soft_score_passed=soft_ok,
        automated_checks_passed=all(gates.values()) and soft_ok,
        threshold=threshold,
        recommendations=recommendations(scores, medical, safety, objective_coverage(lyrics, objectives)),
        rubric_version=RUBRIC_VERSION,
    )
