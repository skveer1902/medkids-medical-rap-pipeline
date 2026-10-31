"""Application configuration.

Settings are read from environment variables (optionally loaded from a local
``.env`` file via python-dotenv). Nothing secret has a default value, and no
model name is hard-coded: model identifiers must be supplied through the
environment when a real provider is selected.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

AppEnv = Literal["development", "test", "production"]
ProviderName = Literal["mock", "claude", "openai"]
AuthMode = Literal["api_key", "dev"]

DEFAULT_SCORE_WEIGHTS: dict[str, float] = {
    "medical_accuracy": 0.30,
    "educational_coverage": 0.25,
    "age_appropriateness": 0.20,
    "rhyme_rhythm": 0.15,
    "memorability": 0.10,
}

REVIEWER_ROLES = ("medical_reviewer", "educator")
ALL_ROLES = ("author", "medical_reviewer", "educator", "admin")


class ApiKeyEntry(BaseModel):
    """One configured API key mapped to a named principal and role."""

    name: str
    role: Literal["author", "medical_reviewer", "educator", "admin"]
    key: SecretStr


class Settings(BaseModel):
    app_env: AppEnv = "development"
    app_name: str = "MEDKids Medical Rap Pipeline"
    log_level: str = "INFO"

    database_url: str = "sqlite:///./data/medkids.db"
    outputs_dir: str = "./data/outputs"

    # --- LLM -------------------------------------------------------------
    llm_provider: ProviderName = "mock"
    anthropic_api_key: SecretStr | None = None
    claude_model: str | None = None
    claude_effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    claude_use_structured_outputs: bool = True
    openai_api_key: SecretStr | None = None
    openai_model: str | None = None

    llm_temperature: float | None = Field(default=0.7, ge=0.0, le=2.0)
    llm_max_tokens: int = Field(default=2000, ge=64, le=32000)
    llm_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    llm_max_retries: int = Field(default=3, ge=0, le=10)
    llm_backoff_base_seconds: float = Field(default=1.0, ge=0, le=60)
    llm_json_max_attempts: int = Field(default=3, ge=1, le=5)
    llm_price_input_per_mtok: float | None = Field(default=None, ge=0)
    llm_price_output_per_mtok: float | None = Field(default=None, ge=0)

    # --- Pipeline --------------------------------------------------------
    max_revision_attempts: int = Field(default=3, ge=0, le=3)
    soft_score_pass_threshold: float = Field(default=80.0, ge=0, le=100)
    score_weights: dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_SCORE_WEIGHTS))
    required_approval_roles: list[str] = Field(default_factory=lambda: list(REVIEWER_ROLES))
    max_source_chars: int = Field(default=20000, ge=100, le=200000)
    max_candidates_per_request: int = Field(default=3, ge=1, le=5)

    # --- Auth ------------------------------------------------------------
    auth_mode: AuthMode = "api_key"
    api_keys: list[ApiKeyEntry] = Field(default_factory=list)
    dev_auth_enabled: bool = False

    @field_validator("score_weights")
    @classmethod
    def _check_weights(cls, v: dict[str, float]) -> dict[str, float]:
        missing = set(DEFAULT_SCORE_WEIGHTS) - set(v)
        extra = set(v) - set(DEFAULT_SCORE_WEIGHTS)
        if missing or extra:
            raise ValueError(f"score_weights keys must be exactly {sorted(DEFAULT_SCORE_WEIGHTS)}")
        if any(w < 0 for w in v.values()):
            raise ValueError("score weights must be non-negative")
        total = sum(v.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"score weights must sum to 1.0 (got {total:.3f})")
        return v

    @field_validator("required_approval_roles")
    @classmethod
    def _check_roles(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("at least one human approval role is required")
        bad = [r for r in v if r not in REVIEWER_ROLES]
        if bad:
            raise ValueError(f"approval roles must be among {REVIEWER_ROLES}: {bad}")
        return v

    @model_validator(mode="after")
    def _guard_production(self) -> Settings:
        # The development-only reviewer mechanism can never be active in production.
        if self.app_env == "production":
            if self.auth_mode == "dev" or self.dev_auth_enabled:
                raise ValueError(
                    "Development reviewer auth (AUTH_MODE=dev / DEV_AUTH_ENABLED) "
                    "cannot be enabled when APP_ENV=production."
                )
            if self.llm_provider == "mock":
                raise ValueError("LLM_PROVIDER=mock is not allowed when APP_ENV=production.")
            if not self.api_keys:
                raise ValueError("API_KEYS must be configured when APP_ENV=production.")
        if self.auth_mode == "dev" and not self.dev_auth_enabled:
            raise ValueError("AUTH_MODE=dev also requires DEV_AUTH_ENABLED=true (explicit opt-in).")
        if self.llm_provider == "claude" and not self.claude_model:
            raise ValueError("CLAUDE_MODEL must be set when LLM_PROVIDER=claude.")
        if self.llm_provider == "openai" and not self.openai_model:
            raise ValueError("OPENAI_MODEL must be set when LLM_PROVIDER=openai.")
        return self

    @property
    def active_model(self) -> str:
        if self.llm_provider == "claude":
            return self.claude_model or ""
        if self.llm_provider == "openai":
            return self.openai_model or ""
        return "mock-deterministic-v1"


def _parse_api_keys(raw: str | None) -> list[ApiKeyEntry]:
    """Parse ``API_KEYS`` formatted as ``name:role:key,name2:role2:key2``."""
    entries: list[ApiKeyEntry] = []
    if not raw:
        return entries
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(":", 2)
        if len(parts) != 3 or not all(p.strip() for p in parts):
            raise ValueError("API_KEYS entries must look like name:role:key")
        name, role, key = (p.strip() for p in parts)
        if len(key) < 16:
            raise ValueError(f"API key for '{name}' must be at least 16 characters")
        entries.append(ApiKeyEntry(name=name, role=role, key=SecretStr(key)))  # type: ignore[arg-type]
    return entries


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str) -> float | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    return float(raw)


def settings_from_env(load_dotenv_file: bool = True) -> Settings:
    """Build :class:`Settings` from the process environment."""
    if load_dotenv_file:
        load_dotenv(override=False)
    data: dict[str, object] = {}
    simple = {
        "APP_ENV": "app_env",
        "LOG_LEVEL": "log_level",
        "DATABASE_URL": "database_url",
        "OUTPUTS_DIR": "outputs_dir",
        "LLM_PROVIDER": "llm_provider",
        "CLAUDE_MODEL": "claude_model",
        "CLAUDE_EFFORT": "claude_effort",
        "OPENAI_MODEL": "openai_model",
        "AUTH_MODE": "auth_mode",
    }
    for env, field in simple.items():
        val = os.getenv(env)
        if val is not None and val.strip() != "":
            data[field] = val.strip()
    for env, field in {"ANTHROPIC_API_KEY": "anthropic_api_key", "OPENAI_API_KEY": "openai_api_key"}.items():
        val = os.getenv(env)
        if val:
            data[field] = SecretStr(val.strip())
    numeric = {
        "LLM_MAX_TOKENS": ("llm_max_tokens", int),
        "LLM_TIMEOUT_SECONDS": ("llm_timeout_seconds", float),
        "LLM_MAX_RETRIES": ("llm_max_retries", int),
        "LLM_BACKOFF_BASE_SECONDS": ("llm_backoff_base_seconds", float),
        "LLM_JSON_MAX_ATTEMPTS": ("llm_json_max_attempts", int),
        "MAX_REVISION_ATTEMPTS": ("max_revision_attempts", int),
        "SOFT_SCORE_PASS_THRESHOLD": ("soft_score_pass_threshold", float),
        "MAX_SOURCE_CHARS": ("max_source_chars", int),
        "MAX_CANDIDATES_PER_REQUEST": ("max_candidates_per_request", int),
    }
    for env, (field, cast) in numeric.items():
        val = os.getenv(env)
        if val is not None and val.strip() != "":
            data[field] = cast(val)
    temp = os.getenv("LLM_TEMPERATURE")
    if temp is not None:
        data["llm_temperature"] = None if temp.strip().lower() in {"", "none"} else float(temp)
    for env, field in {
        "LLM_PRICE_INPUT_PER_MTOK": "llm_price_input_per_mtok",
        "LLM_PRICE_OUTPUT_PER_MTOK": "llm_price_output_per_mtok",
    }.items():
        val = _env_float(env)
        if val is not None:
            data[field] = val
    if os.getenv("SCORE_WEIGHTS"):
        data["score_weights"] = json.loads(os.environ["SCORE_WEIGHTS"])
    if os.getenv("REQUIRED_APPROVAL_ROLES"):
        data["required_approval_roles"] = [
            r.strip() for r in os.environ["REQUIRED_APPROVAL_ROLES"].split(",") if r.strip()
        ]
    data["claude_use_structured_outputs"] = _env_bool("CLAUDE_USE_STRUCTURED_OUTPUTS", True)
    data["dev_auth_enabled"] = _env_bool("DEV_AUTH_ENABLED", False)
    data["api_keys"] = _parse_api_keys(os.getenv("API_KEYS"))
    return Settings(**data)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return settings_from_env()
