"""Structured logging with secret redaction.

Log records carry identifiers and counts only. Lyrics, source documents and
reviewer feedback are never logged, and anything resembling a credential is
masked by :class:`RedactingFilter` as a second line of defence.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),  # Anthropic / OpenAI style keys
    re.compile(r"(?i)(api[_-]?key|authorization|x-api-key|token)(\s*[:=]\s*)([^\s,;]+)"),
]


def redact(text: str) -> str:
    text = _SECRET_PATTERNS[0].sub("[REDACTED]", text)
    return _SECRET_PATTERNS[1].sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - malformed record args
            return True
        record.msg = redact(message)
        record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "ctx", None)
        if isinstance(extra, dict):
            payload.update({k: v for k, v in extra.items() if k not in payload})
        if record.exc_info and record.exc_info[0] is not None:
            payload["exc_type"] = record.exc_info[0].__name__
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    for handler in list(root.handlers):
        if getattr(handler, "_medkids", False):
            root.removeHandler(handler)
    handler = logging.StreamHandler()
    handler._medkids = True  # type: ignore[attr-defined]
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter())
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(logger: logging.Logger, msg: str, level: int = logging.INFO, **ctx: object) -> None:
    """Log an event with structured context (ids, counts, statuses only)."""
    logger.log(level, msg, extra={"ctx": ctx})
