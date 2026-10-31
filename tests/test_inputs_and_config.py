"""Input validation (Stage 1) and configuration safety guards."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings, _parse_api_keys
from app.models.schemas import RapGenerateRequest, ReviewRequest, TopicCreate
from tests.conftest import load_sample


def topic(**kw):
    data = load_sample("heart")
    data.update(kw)
    return TopicCreate(**data)


def test_valid_sample_topics_parse():
    assert topic().topic == "Heart"
    assert TopicCreate(**load_sample("brain")).target_age_min == 7


@pytest.mark.parametrize("kw", [
    {"target_age_min": 8, "target_age_max": 5},      # inverted range
    {"target_age_min": 2},                            # below supported ages
    {"target_age_max": 14},                           # above supported ages
    {"educational_content": "   "},                   # empty content
    {"educational_content": ""},
    {"category": "Astrology"},                        # unsupported category
    {"learning_objectives": []},                      # no objectives
    {"learning_objectives": ["same", "Same"]},        # duplicates
    {"topic": "<script>"},                            # unsafe name
    {"forbidden_claims": [{"claim": "bad regex", "pattern": "(unclosed"}]},
    {"unexpected_field": True},                       # extra fields rejected
])
def test_invalid_topic_inputs_rejected(kw):
    with pytest.raises(ValidationError):
        topic(**kw)


def test_generation_request_limits():
    with pytest.raises(ValidationError):
        RapGenerateRequest(topic_id=1, num_verses=9)
    with pytest.raises(ValidationError):
        RapGenerateRequest(topic_id=1, num_candidates=10)
    with pytest.raises(ValidationError):
        RapGenerateRequest(topic_id=1, style="in the style of a famous rapper")


def test_review_request_requires_feedback_for_reject():
    with pytest.raises(ValidationError):
        ReviewRequest(decision="reject")
    assert ReviewRequest(decision="approve").decision == "approve"


def test_production_refuses_dev_auth_and_mock():
    with pytest.raises(ValidationError, match="cannot be enabled"):
        Settings(app_env="production", auth_mode="dev", dev_auth_enabled=True, llm_provider="claude",
                 claude_model="m", api_keys=_parse_api_keys("a:author:aaaaaaaaaaaaaaaaaaaa"))
    with pytest.raises(ValidationError, match="mock"):
        Settings(app_env="production", api_keys=_parse_api_keys("a:author:aaaaaaaaaaaaaaaaaaaa"))
    with pytest.raises(ValidationError, match="API_KEYS"):
        Settings(app_env="production", llm_provider="claude", claude_model="m")


def test_dev_auth_requires_double_opt_in():
    with pytest.raises(ValidationError, match="DEV_AUTH_ENABLED"):
        Settings(auth_mode="dev")


def test_real_provider_requires_model_name():
    with pytest.raises(ValidationError, match="CLAUDE_MODEL"):
        Settings(llm_provider="claude")


def test_score_weights_must_sum_to_one_and_threshold_configurable():
    with pytest.raises(ValidationError):
        Settings(score_weights={"medical_accuracy": 1, "educational_coverage": 1, "age_appropriateness": 0,
                                "rhyme_rhythm": 0, "memorability": 0})
    s = Settings(soft_score_pass_threshold=65, max_revision_attempts=2)
    assert s.soft_score_pass_threshold == 65 and s.max_revision_attempts == 2
    with pytest.raises(ValidationError):
        Settings(max_revision_attempts=10)  # hard upper bound of 3


def test_api_key_parsing():
    keys = _parse_api_keys("alice:medical_reviewer:abcdefghijklmnop, bob:educator:qrstuvwxyz123456")
    assert [(k.name, k.role) for k in keys] == [("alice", "medical_reviewer"), ("bob", "educator")]
    with pytest.raises(ValueError):
        _parse_api_keys("alice:medical_reviewer:short")
    with pytest.raises(ValueError):
        _parse_api_keys("malformed-entry")
