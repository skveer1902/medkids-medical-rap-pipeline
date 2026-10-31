"""Create the database schema (idempotent).

    python scripts/init_db.py
"""

import _bootstrap  # noqa: F401

from app.core.config import get_settings
from app.models.database import SCHEMA_VERSION, init_db, make_engine


def main() -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    init_db(engine)
    print(f"Schema version {SCHEMA_VERSION} ready at {settings.database_url}")


if __name__ == "__main__":
    main()
