"""Stage 4: generation, structured-output validation and bounded retries."""

from __future__ import annotations

import json

import pytest

from app.core.exceptions import LLMOutputValidationError
from app.llm.mock_provider import MockProvider
from app.services.rap_generator import RapGenerator
from tests.test_prompts_and_age import spec


def good_output(**over) -> str:
    out = {
        "title": "Pump It Up",
        "sections": [
            {"section": "verse_1", "lines": [{"text": "The heart is a muscle!", "fact_ids": ["heart_fact_001"]},
                                             {"text": "Clap along with me!", "fact_ids": []}]},
            {"section": "chorus", "lines": [{"text": "Heart, heart, pump pump pump!", "fact_ids": []},
                                            {"text": "The heart pumps blood through the body!",
                                             "fact_ids": ["heart_fact_002", "invented_fact_999"]}]},
            {"section": "verse_2", "lines": [{"text": "The heart pumps blood through the body!",
                                              "fact_ids": ["heart_fact_002"]},
                                             {"text": "Sing it with me!", "fact_ids": []}]},
            {"section": "outro", "lines": [{"text": "Now you know!", "fact_ids": []},
                                           {"text": "Here we go!", "fact_ids": []}]},
        ],
        "objectives_covered": ["heart_obj_01", "made_up_obj"],
    }
    out.update(over)
    return json.dumps(out)


def gen(mock, attempts=3):
    return RapGenerator(mock, temperature=0.7, max_tokens=1500, json_max_attempts=attempts)


def test_mock_generation_has_required_structure(mock):
    rap = gen(mock).generate(spec())
    names = [s.section for s in rap.lyrics.sections]
    assert names == ["verse_1", "chorus", "verse_2", "outro"]
    ids = [ln.line_id for ln in rap.lyrics.all_lines()]
    assert len(ids) == len(set(ids)) and ids[0] == "v1_l01"
    assert rap.lyrics.arrangement.count("chorus") == 2
    assert rap.prompt_name == "rap_generation" and len(rap.prompt_sha256) == 64
    assert rap.input_tokens > 0 and rap.objectives_covered == ["heart_obj_01"]


def test_unapproved_fact_ids_and_unknown_objectives_are_dropped(mock):
    mock.queue("rap_generation", good_output())
    rap = gen(mock).generate(spec())
    chorus = rap.lyrics.sections[1]
    assert chorus.lines[1].fact_ids == ["heart_fact_002"]
    assert any("invented_fact_999" in n for n in rap.notes)
    assert rap.objectives_covered == ["heart_obj_01"]


def test_malformed_json_is_retried_then_succeeds(mock):
    mock.queue("rap_generation", "Sure! Here are your lyrics: ...", '{"title": "x"}', good_output())
    rap = gen(mock).generate(spec())
    assert rap.generation_config["json_attempts_used"] == 3
    # the retry prompt tells the model what was wrong
    assert "previous_attempt_error" in mock.calls_for("rap_generation")[1].prompt


def test_fenced_json_is_accepted(mock):
    mock.queue("rap_generation", "```json\n" + good_output() + "\n```")
    assert gen(mock).generate(spec()).lyrics.title == "Pump It Up"


def test_persistent_malformed_output_raises_after_limit(mock):
    mock.queue("rap_generation", *["{bad json"] * 5)
    with pytest.raises(LLMOutputValidationError):
        gen(mock, attempts=2).generate(spec())
    assert len(mock.calls_for("rap_generation")) == 2


def test_structural_problems_trigger_retry(mock):
    no_verse2 = json.loads(good_output())
    no_verse2["sections"] = [s for s in no_verse2["sections"] if s["section"] != "verse_2"]
    no_topic_chorus = json.loads(good_output())
    no_topic_chorus["sections"][1]["lines"][0]["text"] = "Pump pump pump!"
    no_topic_chorus["sections"][1]["lines"][1]["text"] = "Pump it up through the body!"
    mock.queue("rap_generation", json.dumps(no_verse2), json.dumps(no_topic_chorus), good_output())
    rap = gen(mock).generate(spec())
    calls = mock.calls_for("rap_generation")
    assert len(calls) == 3
    assert "missing required sections" in calls[1].prompt
    assert "chorus must mention" in calls[2].prompt
    assert rap.lyrics.title == "Pump It Up"


def test_invalid_section_name_rejected_by_schema(mock):
    bad = json.loads(good_output())
    bad["sections"][0]["section"] = "bridge"
    mock.queue("rap_generation", *[json.dumps(bad)] * 3)
    with pytest.raises(LLMOutputValidationError):
        gen(mock).generate(spec())


def test_no_approved_facts_refused(mock):
    with pytest.raises(LLMOutputValidationError):
        gen(mock).generate(spec(approved_facts=[]))


def test_temperature_recorded_as_unsupported_for_claude_like_providers():
    class NoTemp(MockProvider):
        @property
        def supports_temperature(self) -> bool:
            return False

    p = NoTemp()
    rap = RapGenerator(p, temperature=0.7, max_tokens=1000).generate(spec())
    assert rap.generation_config["temperature"] == "not_supported_by_provider"
    assert p.calls_for("rap_generation")[0].temperature is None
