"""Load the Heart and Brain DEMONSTRATION topics into the database.

    python scripts/seed_topics.py

Facts are NOT approved by this script. Approve them through the API as a
medical reviewer, or run scripts/run_demo.py (which uses a labelled
development fixture).
"""

from __future__ import annotations

import json
from pathlib import Path

try:
    import _bootstrap  # noqa: F401  (when run as a script)
except ModuleNotFoundError:  # imported as scripts.<name>
    pass
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.models.database import Topic, init_db, make_engine, make_session_factory
from app.models.schemas import TopicCreate
from app.services.security import Principal
from app.services.topic_service import create_topic

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "sample_topics"
SEED_PRINCIPAL = Principal(name="seed-script", role="author", auth_method="script")


def load_sample(name: str) -> TopicCreate:
    raw = json.loads((SAMPLE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return TopicCreate(**{k: v for k, v in raw.items() if not k.startswith("_")})


def seed(session, settings: Settings, names: tuple[str, ...] = ("heart", "brain")) -> list[Topic]:  # type: ignore[no-untyped-def]
    topics = []
    for name in names:
        data = load_sample(name)
        existing = session.scalar(select(Topic).where(Topic.title == data.topic, Topic.is_demo_data.is_(True)))
        if existing:
            topics.append(existing)
            continue
        topics.append(create_topic(session, data, SEED_PRINCIPAL, settings))
    session.commit()
    return topics


def main() -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    init_db(engine)
    with make_session_factory(engine)() as session:
        for t in seed(session, settings):
            print(f"topic id={t.id} slug={t.slug} (demonstration data, facts not approved)")


if __name__ == "__main__":
    main()
