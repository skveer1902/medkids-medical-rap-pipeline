"""FastAPI application factory.

Run locally:  uvicorn app.main:app --reload
Swagger UI:   http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api import routes_evaluation, routes_generation, routes_review, routes_session, routes_topics
from app.core.config import Settings, get_settings
from app.core.exceptions import LLMError, MedKidsError
from app.core.logging import configure_logging, get_logger, log_event
from app.llm import build_provider
from app.llm.base import LLMProvider
from app.models.database import init_db, make_engine, make_session_factory
from app.models.schemas import HealthResponse

__version__ = "0.1.0"
MAX_BODY_BYTES = 512 * 1024
STATIC_DIR = Path(__file__).resolve().parent / "static"
UI_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
    "base-uri 'none'; form-action 'self'"
)
SAFE_LLM_MESSAGES = {
    "llm_rate_limited": "The language model is rate-limited; retry later.",
    "llm_timeout": "The language model timed out; retry later.",
    "llm_unavailable": "The language model service is unavailable; retry later.",
    "llm_auth_failed": "The language model credentials were rejected; check server configuration.",
    "llm_misconfigured": "The language model provider is not configured correctly.",
    "llm_invalid_output": "The language model returned output that failed validation after the retry limit.",
}
logger = get_logger("app")


def create_app(
    settings: Settings | None = None,
    provider_factory: Callable[[], LLMProvider] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        log_event(logger, "startup", app_env=settings.app_env, provider=settings.llm_provider,
                  auth_mode=settings.auth_mode)
        if settings.auth_mode == "dev":
            log_event(logger, "WARNING: development reviewer auth is enabled (never use in production)")
        yield
        app.state.engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=(
            "Human-supervised pipeline that turns approved MEDKids medical facts into age-appropriate "
            "educational rap lyrics. Automated checks never approve content: a human reviewer must."
        ),
        lifespan=lifespan,
    )
    engine = make_engine(settings.database_url)
    init_db(engine)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.provider_factory = provider_factory or (lambda: build_provider(settings))

    @app.middleware("http")
    async def request_guard(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
            return JSONResponse(status_code=413, content={"error": {
                "code": "payload_too_large", "message": f"request body exceeds {MAX_BODY_BYTES} bytes"}})
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        if request.url.path.startswith("/ui"):
            response.headers["Content-Security-Policy"] = UI_CSP
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(MedKidsError)
    async def medkids_error(request: Request, exc: MedKidsError) -> JSONResponse:
        # LLM errors may wrap provider messages or model output: return only a fixed safe message.
        if isinstance(exc, LLMError):
            message, details = SAFE_LLM_MESSAGES.get(exc.code, "The language model service failed."), {}
        else:
            message, details = exc.message, exc.details
        log_event(logger, "request_error", code=exc.code, status=exc.status_code, path=request.url.path)
        return JSONResponse(status_code=exc.status_code,
                            content={"error": {"code": exc.code, "message": message, "details": details}})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg", ""), "type": e.get("type", "")}
                  for e in exc.errors()]
        return JSONResponse(status_code=422, content={"error": {
            "code": "request_validation_failed", "message": "request body or parameters are invalid",
            "details": {"errors": errors}}})

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        rid = getattr(request.state, "request_id", None)
        logger.error("unhandled_error", exc_info=exc, extra={"ctx": {"request_id": rid, "path": request.url.path}})
        return JSONResponse(status_code=500, content={"error": {
            "code": "internal_error", "message": "an internal error occurred", "details": {"request_id": rid}}})

    @app.get("/api/health", response_model=HealthResponse, tags=["health"])
    def health() -> HealthResponse:
        db_status = "ok"
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            db_status = "error"
        return HealthResponse(status="ok", app_env=settings.app_env, llm_provider=settings.llm_provider,
                              database=db_status, version=__version__)  # type: ignore[arg-type]

    for module in (routes_session, routes_topics, routes_generation, routes_evaluation, routes_review):
        app.include_router(module.router)

    # Web UI (plain HTML/CSS/JS, no build step). Data is still fetched from the authenticated API.
    app.mount("/ui", StaticFiles(directory=STATIC_DIR, html=True), name="ui")

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse(url="/ui/")

    return app


_app: FastAPI | None = None


def __getattr__(name: str) -> FastAPI:
    """Create ``app`` lazily (PEP 562) so importing this module in tests has no side effects."""
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)
