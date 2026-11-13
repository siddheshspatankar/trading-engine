"""
FastAPI application factory.

Creates and configures the FastAPI application with all routes,
middleware, error handlers, and lifespan events.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from src.api.middleware.logging_middleware import RequestLoggingMiddleware
from src.api.routes import health, portfolio, risk, strategies
from src.core.exceptions import TradingEngineError
from src.infrastructure.config import get_settings
from src.observability.logging.setup import configure_logging
from src.observability.metrics.prometheus import start_metrics_server

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup and shutdown hooks."""
    settings = get_settings()

    # Configure logging
    configure_logging(
        log_level=settings.log_level,
        json_logs=not settings.is_development,
    )

    log.info(
        "trading_engine_starting",
        env=settings.env,
        version="0.1.0",
    )

    # Start Prometheus metrics server
    start_metrics_server(port=settings.observability.metrics_port)

    yield  # Application is running

    log.info("trading_engine_shutting_down")
    from src.infrastructure.database import dispose_engine
    await dispose_engine()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="AI-Native Trading Engine",
        description="Production-ready algorithmic trading platform",
        version="0.1.0",
        docs_url="/docs" if not settings.is_live else None,  # Disable docs in live
        redoc_url="/redoc" if not settings.is_live else None,
        lifespan=lifespan,
    )

    # --- Middleware ---
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"] if settings.is_development else [],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
    )

    # --- Routes ---
    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        """Send visitors at the bare URL to the API docs (or health check if docs are off)."""
        return RedirectResponse(url="/docs" if app.docs_url else "/health/")

    app.include_router(health.router)
    app.include_router(portfolio.router)
    app.include_router(strategies.router)
    app.include_router(risk.router)

    # --- Error Handlers ---
    @app.exception_handler(TradingEngineError)
    async def trading_engine_error_handler(
        request: Request, exc: TradingEngineError
    ) -> JSONResponse:
        log.warning(
            "trading_engine_error",
            code=exc.code,
            message=exc.message,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"error": exc.code, "message": exc.message},
        )

    @app.exception_handler(Exception)
    async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_exception", path=request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "INTERNAL_ERROR", "message": "An unexpected error occurred"},
        )

    return app
