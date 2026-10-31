"""Server-side authentication and role-based authorization.

MVP mechanism: static API keys from the ``API_KEYS`` environment variable,
each mapped to a named principal and one role. Keys are compared in constant
time and never logged.

Development-only mechanism: when ``AUTH_MODE=dev`` *and*
``DEV_AUTH_ENABLED=true`` (both required) a caller may identify itself with an
``X-Dev-User: name:role`` header. Settings validation refuses to start the
app with this enabled when ``APP_ENV=production``, and the check below
re-verifies the environment on every request as defence in depth.

Before production, replace both with an identity provider (OIDC/SSO) - see
ARCHITECTURE.md.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from app.core.config import ALL_ROLES, Settings
from app.core.exceptions import AuthenticationError, PermissionDeniedError

PERMISSIONS: dict[str, set[str]] = {
    "read": {"author", "medical_reviewer", "educator", "admin"},
    "author": {"author", "admin"},
    "approve_facts": {"medical_reviewer"},
    "review": {"medical_reviewer", "educator"},
}


@dataclass(frozen=True)
class Principal:
    name: str
    role: str
    auth_method: str = "api_key"


def authenticate(settings: Settings, api_key: str | None, dev_user: str | None) -> Principal:
    if api_key:
        for entry in settings.api_keys:
            if hmac.compare_digest(api_key.encode(), entry.key.get_secret_value().encode()):
                return Principal(name=entry.name, role=entry.role, auth_method="api_key")
        raise AuthenticationError("invalid API key")
    if dev_user:
        if not (settings.auth_mode == "dev" and settings.dev_auth_enabled and settings.app_env != "production"):
            raise AuthenticationError("development authentication is disabled")
        name, _, role = dev_user.partition(":")
        name, role = name.strip(), role.strip()
        if not name or role not in ALL_ROLES:
            raise AuthenticationError(f"X-Dev-User must be 'name:role' with role in {ALL_ROLES}")
        return Principal(name=f"dev:{name}", role=role, auth_method="dev")
    raise AuthenticationError("missing credentials: send an X-API-Key header")


def require(principal: Principal, permission: str) -> Principal:
    if principal.role not in PERMISSIONS[permission]:
        raise PermissionDeniedError(
            f"role '{principal.role}' is not allowed to perform '{permission}' actions"
        )
    return principal
