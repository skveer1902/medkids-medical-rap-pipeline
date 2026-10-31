"""LLM providers and factory."""

from __future__ import annotations

from app.core.config import Settings
from app.llm.base import LLMProvider, LLMRequest, LLMResponse


def build_provider(settings: Settings) -> LLMProvider:
    common = {
        "max_retries": settings.llm_max_retries,
        "backoff_base_seconds": settings.llm_backoff_base_seconds,
        "timeout_seconds": settings.llm_timeout_seconds,
        "price_input_per_mtok": settings.llm_price_input_per_mtok,
        "price_output_per_mtok": settings.llm_price_output_per_mtok,
    }
    if settings.llm_provider == "claude":
        from app.llm.claude_provider import ClaudeProvider

        key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else ""
        return ClaudeProvider(
            settings.claude_model or "",
            key,
            effort=settings.claude_effort,
            use_structured_outputs=settings.claude_use_structured_outputs,
            **common,
        )
    if settings.llm_provider == "openai":
        from app.llm.openai_provider import OpenAIProvider

        key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
        return OpenAIProvider(settings.openai_model or "", key, **common)
    from app.llm.mock_provider import MockProvider

    return MockProvider(**common)


__all__ = ["LLMProvider", "LLMRequest", "LLMResponse", "build_provider"]
