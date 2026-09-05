"""Application factory and ASGI entry point.

``create_app()`` is a factory, not a module-level ``app = FastAPI()``, because
tests need to build an isolated instance with different settings. The module
still exposes ``app`` at the bottom so ``uvicorn orderflow.main:app`` works.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from orderflow.api.health import router as health_router
from orderflow.api.router import api_router
from orderflow.core.config import Environment, Settings, get_settings
from orderflow.core.database import Database
from orderflow.core.errors import register_exception_handlers
from orderflow.core.logging import configure_logging, get_logger
from orderflow.core.middleware import (
    BodySizeLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)

logger = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a fully wired application instance."""
    settings = settings or get_settings()

    configure_logging(
        debug=settings.debug,
        level=logging.DEBUG if settings.debug else logging.INFO,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Own the lifecycle of every long-lived resource.

        Code before ``yield`` runs once on startup, code after it once on
        shutdown. Disposing the engine here is what prevents a redeploy from
        leaving orphaned PostgreSQL backends behind.
        """
        app.state.settings = settings
        app.state.database = Database(settings)
        logger.info(
            "application_started",
            environment=settings.environment.value,
            version=settings.app_version,
            locking_strategy=settings.inventory_locking_strategy,
        )
        try:
            yield
        finally:
            await app.state.database.dispose()
            logger.info("application_stopped")

    expose_docs = not settings.environment.is_production_like

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Distributed order processing platform.",
        lifespan=lifespan,
        docs_url="/docs" if expose_docs else None,
        redoc_url="/redoc" if expose_docs else None,
        openapi_url="/openapi.json" if expose_docs else None,
        responses={422: {"description": "Validation error"}},
    )

    _register_middleware(app, settings)
    register_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    return app


def _register_middleware(app: FastAPI, settings: Settings) -> None:
    """Install middleware. Registration order is reverse execution order.

    Added last = runs first. So the effective inbound order is:
    request context → security headers → CORS → trusted host → gzip → body
    limit → routing. The context ID is assigned before anything can fail, and
    the body limit sits closest to the application, after the cheap header
    rejections have already run.
    """
    app.add_middleware(BodySizeLimitMiddleware, max_body_bytes=settings.max_request_body_bytes)

    app.add_middleware(GZipMiddleware, minimum_size=1024)

    if settings.allowed_hosts and settings.allowed_hosts != ("*",):
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
            expose_headers=["X-Request-ID"],
            max_age=600,
        )

    app.add_middleware(
        SecurityHeadersMiddleware,
        enable_hsts=settings.environment is Environment.PRODUCTION,
    )
    app.add_middleware(RequestContextMiddleware)


app = create_app()
