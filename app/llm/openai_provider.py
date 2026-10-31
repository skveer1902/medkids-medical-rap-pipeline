"""Optional OpenAI provider using the official ``openai`` SDK Responses API.

``client.responses.create(model=..., instructions=..., input=...,
max_output_tokens=..., temperature=..., text={"format": {...}})`` and
``response.output_text``. Some OpenAI reasoning models reject
``temperature``; set ``LLM_TEMPERATURE=none`` for those.
"""

from __future__ import annotations

import time
from typing import Any

from app.core.exceptions import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMError,
    LLMRateLimitError,
    LLMServerError,
    LLMTimeoutError,
)
from app.llm.base import LLMProvider, LLMRequest, LLMResponse
from app.llm.claude_provider import _make_json_schema_strict


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, model: str, api_key: str, *, client: Any | None = None, **kwargs: Any) -> None:
        super().__init__(model, **kwargs)
        if not api_key and client is None:
            raise LLMConfigurationError("OPENAI_API_KEY is not set.")
        if client is not None:
            self._client = client
            self._openai = None
        else:
            try:
                import openai
            except ImportError as exc:  # pragma: no cover
                raise LLMConfigurationError(
                    "The 'openai' package is not installed. Run: pip install openai"
                ) from exc
            self._openai = openai
            self._client = openai.OpenAI(api_key=api_key, timeout=self.timeout_seconds, max_retries=0)

    def _map_error(self, exc: Exception) -> LLMError:
        o = self._openai
        if o is None:
            return LLMServerError("OpenAI call failed.")
        if isinstance(exc, o.RateLimitError):
            return LLMRateLimitError("OpenAI rate limit reached.")
        if isinstance(exc, o.APITimeoutError):
            return LLMTimeoutError("OpenAI request timed out.")
        if isinstance(exc, o.AuthenticationError | o.PermissionDeniedError):
            return LLMAuthenticationError("OpenAI authentication failed; check OPENAI_API_KEY.")
        if isinstance(exc, o.APIConnectionError):
            return LLMServerError("Could not connect to the OpenAI API.")
        if isinstance(exc, o.APIStatusError):
            status = getattr(exc, "status_code", 0) or 0
            if status >= 500:
                return LLMServerError(f"OpenAI API unavailable (HTTP {status}).")
            return LLMError(f"OpenAI API rejected the request (HTTP {status}).")
        return LLMError("Unexpected OpenAI client error.")

    def _complete(self, request: LLMRequest) -> LLMResponse:
        params: dict[str, Any] = {
            "model": self.model,
            "instructions": request.system,
            "input": request.prompt,
            "max_output_tokens": request.max_tokens,
        }
        if request.temperature is not None:
            params["temperature"] = request.temperature
        if request.json_schema:
            params["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": request.task[:60],
                    "schema": _make_json_schema_strict(request.json_schema),
                    "strict": False,
                }
            }
        started = time.monotonic()
        try:
            resp = self._client.responses.create(**params)
        except LLMError:
            raise
        except Exception as exc:
            raise self._map_error(exc) from exc
        latency = int((time.monotonic() - started) * 1000)
        usage = getattr(resp, "usage", None)
        return LLMResponse(
            text=getattr(resp, "output_text", "") or "",
            provider=self.name,
            model=getattr(resp, "model", self.model),
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            latency_ms=latency,
            stop_reason=getattr(resp, "status", None),
        )
