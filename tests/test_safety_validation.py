"""Stage 6: child-safety validation (rules + LLM-assisted), and Stage 7 scoring."""

from __future__ import annotations

import json

import pytest

from app.core.config import DEFAULT_SCORE_WEIGHTS
from app.core.exceptions import LLMTimeoutError
from app.models.schemas import ClaimResult, MedicalAccuracyReport, SafetyReport
from app.services import quality_evaluator
from app.services.safety_validator import SafetyValidator
from tests.conftest import lyrics_from


def check(mock, line: str, band: str = "4-6", vocab: list[str] | None = None):
    lyrics = lyrics_from({"verse_1": [(line, [])], "chorus": [("Heart! Heart!", [])],
                          "verse_2": [("The heart is a muscle!", ["f1"])]})
    return SafetyValidator(mock).validate(topic="Heart", band=band, lyrics=lyrics, required_vocabulary=vocab or [])


@pytest.mark.parametrize("line,category,severity", [
    ("Blood goes gushing out, so gory!", "graphic_medical", "critical"),
    ("If it stops you could die tonight!", "frightening", "major"),
    ("Only a stupid kid forgets!", "inappropriate_vocabulary", "major"),
    ("Take two pills when your chest feels tight!", "unsafe_health_advice", "critical"),
    ("Have 500 mg and you'll be fine!", "unsafe_health_advice", "critical"),
    ("Don't tell a grown-up if you feel sick!", "unsafe_health_advice", "critical"),
    ("Skip breakfast to make your heart strong!", "unsafe_health_advice", "critical"),
    ("Beets are a cure, you'll never get sick!", "misleading_health_claim", "major"),
    ("Doctors are men and nurses are women!", "stereotype", "major"),
    ("Boys can't be gentle, that's a fact!", "stereotype", "major"),
    ("Fat kids have lazy hearts!", "discriminatory", "critical"),
])
def test_rule_categories(mock, line, category, severity):
    r = check(mock, line)
    hits = [f for f in r.findings if f.category == category]
    assert hits and hits[0].severity == severity and hits[0].source == "rule"
    assert r.status == "failed" and "v1_l01" in r.failing_line_ids


def test_negated_fright_word_is_only_minor(mock):
    r = check(mock, "Don't be scared, it's just a beat!")
    f = next(f for f in r.findings if f.category == "frightening")
    assert f.severity == "minor" and r.status == "requires_review"


def test_clean_lyrics_pass(mock):
    assert check(mock, "Clap your hands and stomp your feet!").status == "passed"


def test_complexity_is_age_band_specific(mock):
    young = check(mock, "The myocardium pumps blood!", band="4-6")
    assert any(f.category == "unnecessary_complexity" and f.severity == "major" for f in young.findings)
    older = check(mock, "Arteries carry blood away from the heart!", band="7-10")
    assert older.status == "passed"
    allowed = check(mock, "Oxygen rides along in the blood!", band="4-6", vocab=["oxygen"])
    assert allowed.status == "passed"
    long_line = check(mock, "The heart is a muscle that works so hard every single day for you", band="4-6")
    assert any("words; target" in f.explanation for f in long_line.findings)


def test_llm_subjective_findings_are_merged_and_validated(mock):
    mock.queue("safety_validation", json.dumps({"findings": [
        {"line_id": "v1_l01", "category": "frightening", "severity": "major",
         "explanation": "Implied threat about the heart stopping."},
        {"line_id": "bogus", "category": "other", "severity": "minor", "explanation": "general tone"}]}))
    r = check(mock, "Your heart could quit if you don't listen!")
    llm = [f for f in r.findings if f.source == "llm"]
    assert len(llm) == 2 and llm[1].line_id is None  # unknown line id nulled
    assert r.status == "failed"


def test_llm_unavailable_means_requires_review_not_pass(mock):
    mock.queue("safety_validation", *[LLMTimeoutError("slow")] * 10)
    r = check(mock, "Clap your hands and stomp your feet!")
    assert r.llm_review_available is False and r.status == "requires_review"


# --- Stage 7 scoring ---------------------------------------------------------------------------

def reports(status="passed", claims=None):
    med = MedicalAccuracyReport(status=status, claims=claims or [
        ClaimResult(line_id="v1_l01", claim="x", status="supported", source_fact_id="f1")],
        failing_line_ids=[], review_line_ids=[], summary={})
    return med, SafetyReport(status="passed", findings=[], failing_line_ids=[])


def score(lyrics, med, saf, threshold=80.0):
    return quality_evaluator.evaluate(
        lyrics=lyrics, topic="Heart", band="4-6",
        objectives=[{"objective_id": "o1", "text": "The heart is a muscle."},
                    {"objective_id": "o2", "text": "The heart pumps blood."}],
        declared_objectives=["o1", "o2"], medical=med, safety=saf, num_verses=2,
        weights=DEFAULT_SCORE_WEIGHTS, threshold=threshold)


LYRICS = lyrics_from({
    "verse_1": [("The heart is a muscle, strong and neat!", ["f1"]), ("Clap your hands and stomp your feet!", [])],
    "chorus": [("Heart! Heart! Sing it loud!", []), ("Heart! Heart! Make us proud!", [])],
    "verse_2": [("Your heart pumps blood all day!", ["f2"]), ("Sing it with me, hip hooray!", [])],
})


def test_scores_weights_and_explanations():
    med, saf = reports()
    ev = score(LYRICS, med, saf)
    assert set(ev.scores) == set(DEFAULT_SCORE_WEIGHTS)
    assert ev.scores["educational_coverage"].score == 100.0
    assert ev.scores["rhyme_rhythm"].score > 60
    assert ev.scores["memorability"].score == 100.0
    assert ev.total_score == pytest.approx(sum(s.score * s.weight for s in ev.scores.values()), abs=0.1)
    assert all(s.explanation for s in ev.scores.values())
    assert ev.automated_checks_passed


def test_medical_failure_blocks_regardless_of_score():
    claims = [ClaimResult(line_id="v1_l01", claim="x", status="contradicted", source_fact_id=None)]
    med, saf = reports("failed", claims)
    ev = score(LYRICS, med, saf, threshold=0)
    assert ev.scores["medical_accuracy"].score == 0
    assert ev.hard_gates["medical_accuracy_not_failed"] is False
    assert ev.automated_checks_passed is False  # even with threshold 0


def test_threshold_is_configurable_and_recommendations_given():
    med, saf = reports()
    weak = lyrics_from({"verse_1": [("Pumpkin", [])], "chorus": [("La", [])], "verse_2": [("Banana", [])]})
    ev = score(weak, med, saf, threshold=99)
    assert not ev.soft_score_passed and ev.recommendations
    assert any("objectives" in r for r in ev.recommendations)
    assert ev.hard_gates["cites_approved_facts"] is False
