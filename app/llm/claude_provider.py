"""Anthropic Claude provider (official ``anthropic`` Python SDK, Messages API).

Notes on current SDK conventions (anthropic>=2.3):

* ``client.messages.create(model=..., max_tokens=..., system=..., messages=[...])``.
* Structured JSON output is requested with
  ``output_config={"format": {"type": "json_schema", "schema": ...}}``. The
  pipeline still validates every response with Pydantic regardless.
* The current SDK no longer exposes sampling parameters such as
  ``temperature`` on ``messages.create``; ``LLM_TEMPERATURE`` is therefore
  recorded as "not supported" for Claude rather than silently sent.
* SDK-level retries are disabled (``max_retries=0``) because retry/backoff is
  handled uniformly in :class:`app.llm.base.LLMProvider`.
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

_UNSUPPORTED_KEYWORDS = {
    "minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum",
    "exclusiveMinimum", "exclusiveMaximum", "pattern", "default",
}


def _make_json_schema_strict(schema: dict[str, Any]) -> dict[str, Any]:
    """Fallback schema transform: ``additionalProperties: false`` on objects and
    drop constraint keywords that structured-output APIs may reject. (Local
    Pydantic validation still enforces every constraint.)"""

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for k, v in node.items():
                if k in ("properties", "$defs") and isinstance(v, dict):
                    out[k] = {name: walk(sub) for name, sub in v.items()}  # keys are field names
                elif k not in _UNSUPPORTED_KEYWORDS:
                    out[k] = walk(v)
            node = out
            if node.get("type") == "object" and "additionalProperties" not in node:
                node["additionalProperties"] = False
            return node
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


class ClaudeProvider(LLMProvider):
    name = "claude"

    def __init__(
        self,
        model: str,
        api_key: str,
        *,
        effort: str | None = None,
        use_structured_outputs: bool = True,
        client: Any | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(model, **kwargs)
        if not api_key and client is None:
            raise LLMConfigurationError("ANTHROPIC_API_KEY is not set.")
        self.effort = effort
        self.use_structured_outputs = use_structured_outputs
        if client is not None:
            self._client = client
            self._anthropic = None
        else:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - depends on install
                raise LLMConfigurationError(
                    "The 'anthropic' package is not installed. Run: pip install -r requirements.txt"
                ) from exc
            self._anthropic = anthropic
            self._client = anthropic.Anthropic(
                api_key=api_key, timeout=self.timeout_seconds, max_retries=0
            )

    @property
    def supports_temperature(self) -> bool:
        return False

    def _map_error(self, exc: Exception) -> LLMError:
        a = self._anthropic
        if a is None:
            return LLMServerError("Claude call failed.")
        if isinstance(exc, a.RateLimitError):
            return LLMRateLimitError("Claude rate limit reached.")
        if isinstance(exc, a.APITimeoutError):
            return LLMTimeoutError("Claude request timed out.")
        if isinstance(exc, a.AuthenticationError | a.PermissionDeniedError):
            return LLMAuthenticationError("Claude authentication/permission failed; check ANTHROPIC_API_KEY.")
        if isinstance(exc, a.APIConnectionError):
            return LLMServerError("Could not connect to the Claude API.")
        if isinstance(exc, a.APIStatusError):
            status = getattr(exc, "status_code", 0) or 0
            if status == 429:
                return LLMRateLimitError("Claude rate limit reached.")
            if status >= 500:  # includes 529 overloaded
                return LLMServerError(f"Claude API unavailable (HTTP {status}).")
            return LLMError(f"Claude API rejected the request (HTTP {status}).")
        return LLMError("Unexpected Claude client error.")

    def _complete(self, request: LLMRequest) -> LLMResponse:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.prompt}],
        }
        output_config: dict[str, Any] = {}
        if self.effort:
            output_config["effort"] = self.effort
        if self.use_structured_outputs and request.json_schema:
            transform = getattr(self._anthropic, "transform_schema", None)
            schema = (
                transform(request.json_schema)
                if callable(transform)
                else _make_json_schema_strict(request.json_schema)
            )
            output_config["format"] = {"type": "json_schema", "schema": schema}
        if output_config:
            params["output_config"] = output_config

        started = time.monotonic()
        try:
            message = self._client.messages.create(**params)
        except LLMError:
            raise
        except Exception as exc:  # SDK exceptions are mapped, never leaked
            raise self._map_error(exc) from exc
        latency = int((time.monotonic() - started) * 1000)

        text = "".join(
            getattr(block, "text", "") for block in message.content if getattr(block, "type", "") == "text"
        )
        usage = getattr(message, "usage", None)
        return LLMResponse(
            text=text,
            provider=self.name,
            model=getattr(message, "model", self.model),
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            latency_ms=latency,
            stop_reason=getattr(message, "stop_reason", None),
        )
