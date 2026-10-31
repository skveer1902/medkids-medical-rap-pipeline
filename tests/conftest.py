"""Shared fixtures. Everything runs offline against MockProvider + in-memory SQLite."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import ApiKeyEntry, Settings
from app.llm.mock_provider import MockProvider
from app.main import create_app
from app.models.database import init_db, make_engine, make_session_factory
from app.models.schemas import FactApprovalRequest, LyricLine, Lyrics, LyricSection, TopicCreate
from app.services.fact_extractor import FactExtractor, approve_facts
from app.services.pipeline import Pipeline
from app.services.security import Principal
from app.services.topic_service import create_topic

ROOT = Path(__file__).resolve().parent.parent

KEYS = {
    "author": "author-key-0123456789",
    "medical": "medical-key-0123456789",
    "medical2": "medical2-key-012345678",
    "educator": "educator-key-012345678",
    "admin": "admin-key-0123456789ab",
}

AUTHOR = Principal("ann-author", "author")
MEDICAL = Principal("dr-mo", "medical_reviewer")
EDUCATOR = Principal("ed-teacher", "educator")


def make_settings(tmp_path: Path, **overrides) -> Settings:
    base = dict(
        app_env="test",
        database_url="sqlite://",
        outputs_dir=str(tmp_path / "outputs"),
        llm_provider="mock",
        log_level="WARNING",
        api_keys=[
            ApiKeyEntry(name="ann-author", role="author", key=SecretStr(KEYS["author"])),
            ApiKeyEntry(name="dr-mo", role="medical_reviewer", key=SecretStr(KEYS["medical"])),
            ApiKeyEntry(name="dr-lee", role="medical_reviewer", key=SecretStr(KEYS["medical2"])),
            ApiKeyEntry(name="ed-teacher", role="educator", key=SecretStr(KEYS["educator"])),
            ApiKeyEntry(name="root", role="admin", key=SecretStr(KEYS["admin"])),
        ],
    )
    base.update(overrides)
    return Settings(**base)


def h(who: str) -> dict[str, str]:
    return {"X-API-Key": KEYS[who]}


def load_sample(name: str) -> dict:
    raw = json.loads((ROOT / "data" / "sample_topics" / f"{name}.json").read_text())
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def lyrics_from(spec: dict[str, list[tuple[str, list[str]]]], title: str = "Test Rap") -> Lyrics:
    prefixes = {"intro": "in", "chorus": "ch", "outro": "out"}
    sections = []
    for name, lines in spec.items():
        p = prefixes.get(name) or "v" + name.split("_")[1]
        sections.append(LyricSection(section=name, lines=[
            LyricLine(line_id=f"{p}_l{i:02d}", text=t, fact_ids=f) for i, (t, f) in enumerate(lines, start=1)
        ]))
    return Lyrics(title=title, sections=sections, arrangement=["verse_1", "chorus", "verse_2", "chorus"])


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def mock() -> MockProvider:
    return MockProvider()


@pytest.fixture
def session(settings: Settings):
    engine = make_engine(settings.database_url)
    init_db(engine)
    s = make_session_factory(engine)()
    yield s
    s.close()
    engine.dispose()


@pytest.fixture
def pipeline(settings: Settings, mock: MockProvider) -> Pipeline:
    return Pipeline(settings, mock)


@pytest.fixture
def heart_topic(session, settings):
    topic = create_topic(session, TopicCreate(**load_sample("heart")), AUTHOR, settings)
    session.commit()
    return topic


@pytest.fixture
def approved_heart(session, heart_topic, mock):
    _, facts = FactExtractor(mock).extract(session, heart_topic)
    approve_facts(session, heart_topic,
                  FactApprovalRequest(approve_fact_ids=[f.fact_key for f in facts if f.status == "candidate"]),
                  MEDICAL)
    session.commit()
    mock.requests.clear()
    return heart_topic


@pytest.fixture
def app(settings: Settings, mock: MockProvider):
    return create_app(settings, provider_factory=lambda: mock)


@pytest.fixture
def client(app) -> TestClient:
    with TestClient(app) as c:
        yield c
