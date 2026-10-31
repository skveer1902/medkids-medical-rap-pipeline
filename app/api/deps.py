"""FastAPI dependencies: settings, DB session, pipeline, authentication."""

from __future__ import annotations

from collections.abc import Callable, Iterator

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.services.pipeline import Pipeline
from app.services.security import Principal, authenticate, require


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[Session]:
    session: Session = request.app.state.session_factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_pipeline(request: Request) -> Pipeline:
    # A provider per request keeps token-usage accounting per run isolated.
    return Pipeline(request.app.state.settings, request.app.state.provider_factory())


def get_principal(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_dev_user: str | None = Header(default=None, alias="X-Dev-User"),
) -> Principal:
    return authenticate(request.app.state.settings, x_api_key, x_dev_user)


def requires(permission: str) -> Callable[[Principal], Principal]:
    def _dep(principal: Principal = Depends(get_principal)) -> Principal:
        return require(principal, permission)

    _dep.__name__ = f"requires_{permission}"
    return _dep
