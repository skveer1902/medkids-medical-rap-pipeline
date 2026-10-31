"""Create a local .env (development, mock LLM) with freshly generated API keys.

    python scripts/make_local_env.py

Never overwrites an existing .env.
"""

from __future__ import annotations

import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    env = ROOT / ".env"
    if env.exists():
        print(".env already exists - leaving it unchanged.")
        return 0
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    keys = {
        "author": ("local-author", secrets.token_urlsafe(18)),
        "medical_reviewer": ("local-medical-reviewer", secrets.token_urlsafe(18)),
        "educator": ("local-educator", secrets.token_urlsafe(18)),
    }
    api_keys = ",".join(f"{name}:{role}:{key}" for role, (name, key) in keys.items())
    content = re.sub(r"^API_KEYS=.*$", f"API_KEYS={api_keys}", template, flags=re.M)
    env.write_text(content, encoding="utf-8")
    print("Created .env (APP_ENV=development, LLM_PROVIDER=mock).")
    print("Local API keys (send as the X-API-Key header in Swagger):")
    for role, (_, key) in keys.items():
        print(f"  {role:17s} {key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
