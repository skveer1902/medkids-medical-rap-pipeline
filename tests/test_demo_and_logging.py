"""Demo script end-to-end + logging hygiene."""

from __future__ import annotations

import logging

from app.core.logging import RedactingFilter, redact


def test_demo_runs_offline_and_never_approves(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("AUTH_MODE", raising=False)
    from scripts.run_demo import DEV_FIXTURE_REVIEWER, run

    results = run(tmp_path / "demo.db", tmp_path / "out")
    assert [r["topic"] for r in results] == ["Heart", "Brain"]
    for r in results:
        assert r["status"] in {"pending_review", "validation_failed"}
        assert r["is_demo_data"] is True and "NOT a human" in r["fact_approval"]
        assert (tmp_path / "out" / f"demo_{r['topic'].lower()}.json").exists()
    assert "not a human" in DEV_FIXTURE_REVIEWER.name


def test_secrets_are_redacted_from_logs():
    assert "sk-ant-abcdef123456" not in redact("calling with sk-ant-abcdef123456")
    assert "supersecret" not in redact("X-API-Key: supersecret")
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "token=%s", ("abc123",), None)
    RedactingFilter().filter(record)
    assert "abc123" not in record.getMessage()
