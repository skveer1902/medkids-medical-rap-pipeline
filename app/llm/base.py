"""Provider-neutral LLM interface.

Concrete providers implement only :meth:`LLMProvider._complete` (one raw API
call). This base class adds, uniformly for every provider:

* retry with exponential backoff (+ jitter) for rate limits, timeouts and 5xx;
* structured JSON generation with Pydantic validation and a bounded number of
  repair attempts for malformed output;
* token-usage tracking and cost estimation when pricing is configured.
"""

from __future__ import annotations

import json
import random
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.exceptions import LLMError, LLMOutputValidationError
from app.core.logging import get_logger, log_event

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


@dataclass
class LLMRequest:
    task: str  # pipeline stage, e.g. "rap_generation"
    system: str
    prompt: str
    temperature: float | None = None
    max_tokens: int = 2000
    json_schema: dict[str, Any] | None = None  # used by providers with native structured output
    prompt_name: str | None = None
    prompt_version: str | None = None
    # Structured copy of the inputs. Real providers ignore it (they only see
    # system/prompt); the deterministic MockProvider uses it to build outputs.
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    stop_reason: str | None = None


@dataclass
class CallRecord:
    task: str
    provider: str
    model: str
    prompt_name: str | None
    prompt_version: str | None
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float | None
    latency_ms: int
    attempts: int
    success: bool
    error_code: str | None = None


@dataclass
class UsageTracker:
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float | None = None
    calls: list[CallRecord] = field(default_factory=list)

    def record(self, rec: CallRecord) -> None:
        self.calls.append(rec)
        self.input_tokens += rec.input_tokens
        self.output_tokens += rec.output_tokens
        if rec.estimated_cost_usd is not None:
            self.estimated_cost_usd = (self.estimated_cost_usd or 0.0) + rec.estimated_cost_usd

    def drain(self) -> list[CallRecord]:
        calls, self.calls = self.calls, []
        return calls


@dataclass
class StructuredResult:
    data: Any
    response: LLMResponse
    attempts: int
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float | None


_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def extract_json_text(text: str) -> str:
    """Return the JSON object embedded in a model response.

    Handles bare JSON, fenced ```json blocks and leading/trailing prose by
    locating the outermost balanced ``{...}``.
    """
    stripped = text.strip()
    fence = _FENCE_RE.match(stripped)
    if fence:
        stripped = fence.group(1).strip()
    if stripped.startswith("{"):
        return stripped
    start = stripped.find("{")
    if start == -1:
        raise ValueError("no JSON object found in model output")
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(stripped)):
        ch = stripped[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return stripped[start : i + 1]
    raise ValueError("unbalanced JSON object in model output")


class LLMProvider(ABC):
    name: str = "base"

    def __init__(
        self,
        model: str,
        *,
        max_retries: int = 3,
        backoff_base_seconds: float = 1.0,
        timeout_seconds: float = 60.0,
        price_input_per_mtok: float | None = None,
        price_output_per_mtok: float | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not model:
            raise ValueError("model must be provided via configuration")
        self.model = model
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self.timeout_seconds = timeout_seconds
        self.price_input_per_mtok = price_input_per_mtok
        self.price_output_per_mtok = price_output_per_mtok
        self.usage = UsageTracker()
        self._sleep = sleep

    # -- to implement -----------------------------------------------------

    @abstractmethod
    def _complete(self, request: LLMRequest) -> LLMResponse:
        """Perform exactly one API call. Raise app.core.exceptions.LLM* errors."""

    @property
    def supports_temperature(self) -> bool:
        return True

    # -- cost ------------------------------------------------------------

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float | None:
        if self.price_input_per_mtok is None or self.price_output_per_mtok is None:
            return None
        return round(
            input_tokens / 1_000_000 * self.price_input_per_mtok
            + output_tokens / 1_000_000 * self.price_output_per_mtok,
            6,
        )

    # -- public API ------------------------------------------------------

    def generate_text(self, request: LLMRequest) -> LLMResponse:
        """One logical call with retry/backoff on transient failures."""
        attempt = 0
        while True:
            attempt += 1
            try:
                response = self._complete(request)
            except LLMError as exc:
                if exc.retryable and attempt <= self.max_retries:
                    delay = self.backoff_base_seconds * (2 ** (attempt - 1))
                    delay += random.uniform(0, self.backoff_base_seconds * 0.25)
                    log_event(
                        logger,
                        "llm_retry",
                        task=request.task,
                        provider=self.name,
                        attempt=attempt,
                        error=exc.code,
                        delay_s=round(delay, 2),
                    )
                    self._sleep(delay)
                    continue
                self.usage.record(
                    CallRecord(
                        task=request.task,
                        provider=self.name,
                        model=self.model,
                        prompt_name=request.prompt_name,
                        prompt_version=request.prompt_version,
                        input_tokens=0,
                        output_tokens=0,
                        estimated_cost_usd=None,
                        latency_ms=0,
                        attempts=attempt,
                        success=False,
                        error_code=exc.code,
                    )
                )
                log_event(logger, "llm_call_failed", task=request.task, provider=self.name, error=exc.code)
                raise
            self.usage.record(
                CallRecord(
                    task=request.task,
                    provider=self.name,
                    model=response.model,
                    prompt_name=request.prompt_name,
                    prompt_version=request.prompt_version,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    estimated_cost_usd=self.estimate_cost(response.input_tokens, response.output_tokens),
                    latency_ms=response.latency_ms,
                    attempts=attempt,
                    success=True,
                )
            )
            log_event(
                logger,
                "llm_call_ok",
                task=request.task,
                provider=self.name,
                model=response.model,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                latency_ms=response.latency_ms,
            )
            return response

    def generate_json(
        self, request: LLMRequest, schema: type[T], *, max_attempts: int = 3
    ) -> StructuredResult:
        """Generate output that must validate against ``schema``.

        Malformed or schema-violating output is retried (with the validation
        error appended to the prompt) at most ``max_attempts`` times in total,
        after which :class:`LLMOutputValidationError` is raised.
        """
        if request.json_schema is None:
            request.json_schema = schema.model_json_schema()
        base_prompt = request.prompt
        last_error = ""
        in_tok = out_tok = 0
        cost: float | None = None
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                request.prompt = (
                    f"{base_prompt}\n\n<previous_attempt_error>\nYour previous reply was rejected: "
                    f"{last_error[:500]}\nReturn ONLY one JSON object that matches the required "
                    "schema. No prose, no markdown.\n</previous_attempt_error>"
                )
            response = self.generate_text(request)
            in_tok += response.input_tokens
            out_tok += response.output_tokens
            c = self.estimate_cost(response.input_tokens, response.output_tokens)
            if c is not None:
                cost = (cost or 0.0) + c
            try:
                payload = json.loads(extract_json_text(response.text))
                data = schema.model_validate(payload)
            except (ValueError, ValidationError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                log_event(
                    logger,
                    "llm_output_invalid",
                    task=request.task,
                    attempt=attempt,
                    error_type=type(exc).__name__,
                )
                continue
            request.prompt = base_prompt
            return StructuredResult(
                data=data,
                response=response,
                attempts=attempt,
                input_tokens=in_tok,
                output_tokens=out_tok,
                estimated_cost_usd=cost,
            )
        request.prompt = base_prompt
        raise LLMOutputValidationError(
            f"Model output for '{request.task}' failed schema validation after {max_attempts} attempt(s).",
            details={"task": request.task, "attempts": max_attempts, "last_error": last_error[:500]},
        )
