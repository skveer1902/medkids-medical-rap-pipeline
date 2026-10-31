"""Provider abstraction: retries/backoff, error handling, JSON parsing, usage & cost, real-SDK adapters."""

from __future__ import annotations

import types

import pytest
from pydantic import BaseModel

from app.core.config import Settings
from app.core.exceptions import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMOutputValidationError,
    LLMRateLimitError,
    LLMServerError,
    LLMTimeoutError,
)
from app.llm import build_provider
from app.llm.base import LLMRequest, extract_json_text
from app.llm.claude_provider import ClaudeProvider
from app.llm.mock_provider import MockProvider
from app.llm.openai_provider import OpenAIProvider


class Out(BaseModel):
    answer: int


def req() -> LLMRequest:
    return LLMRequest(task="t", system="s", prompt="p", temperature=0.3, max_tokens=100)


def test_retry_with_exponential_backoff_then_success():
    sleeps: list[float] = []
    p = MockProvider(max_retries=3, backoff_base_seconds=1.0, sleep=sleeps.append)
    p.queue("t", LLMRateLimitError("429"), LLMTimeoutError("slow"), '{"answer": 1}')
    res = p.generate_json(req(), Out)
    assert res.data.answer == 1
    assert len(sleeps) == 2 and 1.0 <= sleeps[0] < 1.3 and 2.0 <= sleeps[1] < 2.3
    assert p.usage.calls[-1].attempts == 3


def test_retries_exhausted_raises():
    p = MockProvider(max_retries=2, sleep=lambda s: None)
    p.queue("t", *[LLMServerError("down")] * 5)
    with pytest.raises(LLMServerError):
        p.generate_text(req())
    assert len(p.requests) == 3  # 1 + 2 retries
    assert p.usage.calls[-1].success is False and p.usage.calls[-1].error_code == "llm_unavailable"


def test_non_retryable_errors_are_not_retried():
    p = MockProvider(max_retries=5, sleep=lambda s: None)
    p.queue("t", LLMAuthenticationError("bad key"))
    with pytest.raises(LLMAuthenticationError):
        p.generate_text(req())
    assert len(p.requests) == 1


def test_json_parsing_failures_bounded():
    p = MockProvider()
    p.queue("t", "nope", '{"answer": "not-int"}', '{"answer": 2')
    with pytest.raises(LLMOutputValidationError) as ei:
        p.generate_json(req(), Out, max_attempts=3)
    assert ei.value.details["attempts"] == 3 and len(p.requests) == 3


@pytest.mark.parametrize("text", ['{"a": 1}', '```json\n{"a": 1}\n```', 'Here you go: {"a": 1} hope it helps',
                                  '{"a": "brace } inside"}'])
def test_extract_json_text(text):
    import json
    assert "a" in json.loads(extract_json_text(text))


def test_extract_json_text_errors():
    with pytest.raises(ValueError):
        extract_json_text("no json here")
    with pytest.raises(ValueError):
        extract_json_text('prefix {"a": 1')  # unbalanced object after prose


def test_usage_and_cost_tracking():
    p = MockProvider(price_input_per_mtok=3.0, price_output_per_mtok=15.0)
    p.queue("t", '{"answer": 3}')
    p.generate_json(LLMRequest(task="t", system="s" * 400, prompt="p" * 400), Out)
    assert p.usage.input_tokens == 200 and p.usage.output_tokens >= 1
    assert p.usage.estimated_cost_usd == pytest.approx(200 / 1e6 * 3 + p.usage.output_tokens / 1e6 * 15)
    assert MockProvider().estimate_cost(1000, 1000) is None  # no pricing configured


def test_build_provider_defaults_to_mock_and_requires_keys():
    assert build_provider(Settings()).name == "mock"
    with pytest.raises(LLMConfigurationError):
        build_provider(Settings(llm_provider="claude", claude_model="some-model"))  # no API key


# --- Claude adapter against a fake SDK client ----------------------------------------------------

class FakeErr(Exception):
    def __init__(self, status_code=0):
        super().__init__("x")
        self.status_code = status_code


def fake_anthropic_module():
    names = ["RateLimitError", "APITimeoutError", "AuthenticationError", "PermissionDeniedError",
             "APIConnectionError", "APIStatusError"]
    mod = types.SimpleNamespace(**{n: type(n, (FakeErr,), {}) for n in names})
    mod.transform_schema = lambda s: {"transformed": True, **s}
    return mod


class FakeMessages:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text='{"answer": 7}')],
            usage=types.SimpleNamespace(input_tokens=11, output_tokens=5), model="claude-test", stop_reason="end_turn")


def claude(behaviour=None, **kw):
    messages = FakeMessages(behaviour)
    p = ClaudeProvider("claude-test", "key", client=types.SimpleNamespace(messages=messages),
                       sleep=lambda s: None, **kw)
    p._anthropic = fake_anthropic_module()
    return p, messages


def test_claude_request_uses_current_sdk_conventions():
    p, messages = claude(effort="low")
    res = p.generate_json(req(), Out)
    assert res.data.answer == 7 and res.input_tokens == 11
    kw = messages.kwargs
    assert kw["model"] == "claude-test" and kw["max_tokens"] == 100 and kw["system"] == "s"
    assert kw["messages"] == [{"role": "user", "content": "p"}]
    assert "temperature" not in kw  # not part of the current Messages API surface in the SDK
    assert kw["output_config"]["effort"] == "low"
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["output_config"]["format"]["schema"]["transformed"] is True
    assert p.supports_temperature is False


@pytest.mark.parametrize("name,status,expected", [
    ("RateLimitError", 429, LLMRateLimitError),
    ("APITimeoutError", 0, LLMTimeoutError),
    ("AuthenticationError", 401, LLMAuthenticationError),
    ("APIConnectionError", 0, LLMServerError),
    ("APIStatusError", 529, LLMServerError),  # overloaded
])
def test_claude_error_mapping(name, status, expected):
    p, messages = claude()
    messages.behaviour = getattr(p._anthropic, name)(status)
    p.max_retries = 1
    with pytest.raises(expected):
        p.generate_text(req())


def test_openai_adapter_uses_responses_api():
    captured = {}

    class Responses:
        def create(self, **kw):
            captured.update(kw)
            return types.SimpleNamespace(output_text='{"answer": 4}', model="gpt-test", status="completed",
                                         usage=types.SimpleNamespace(input_tokens=9, output_tokens=3))

    p = OpenAIProvider("gpt-test", "key", client=types.SimpleNamespace(responses=Responses()))
    assert p.generate_json(req(), Out).data.answer == 4
    assert captured["instructions"] == "s" and captured["input"] == "p"
    assert captured["max_output_tokens"] == 100 and captured["temperature"] == 0.3
    assert captured["text"]["format"]["type"] == "json_schema"
