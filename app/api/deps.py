"""FastAPI dependencies: settings, DB session, pipeline, authentication."""

from __future__ import annotations

from collections.abc import Callable, Iterator

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.services.pipeline import Pipeline
from app.services.security import Principal, authenticate, require


def get_settings_dep(request: Request) -> Settings:
    """Return the app's settings so routes can use them."""
    return request.app.state.settings


def get_db(request: Request) -> Iterator[Session]:
    """Open a database session for one request; roll back on error and always close it."""
    session: Session = request.app.state.session_factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_pipeline(request: Request) -> Pipeline:
    """Build a fresh Pipeline (with its own AI provider) for this request."""
    # A provider per request keeps token-usage accounting per run isolated.
    return Pipeline(request.app.state.settings, request.app.state.provider_factory())


def get_principal(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_dev_user: str | None = Header(default=None, alias="X-Dev-User"),
) -> Principal:
    """Work out who is calling, from the API key or the local-mode user header."""
    return authenticate(request.app.state.settings, x_api_key, x_dev_user)


def requires(permission: str) -> Callable[[Principal], Principal]:
    """Make a FastAPI dependency that only lets callers with the given permission through."""
    def _dep(principal: Principal = Depends(get_principal)) -> Principal:
        """Check the caller has the permission and return them."""
        return require(principal, permission)

    _dep.__name__ = f"requires_{permission}"
    return _dep
