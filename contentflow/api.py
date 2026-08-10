from __future__ import annotations

import hmac
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import db
from .migrate import upgrade_database
from .object_storage import build_object_storage
from .observability import ObservabilityMetrics
from .routers import (
    admin,
    assets,
    auth,
    campaigns,
    channels,
    contents,
    dashboard,
    jobs,
    knowledge,
    metrics,
    publishing,
    runs,
)
from .settings import Settings, get_settings


logger = logging.getLogger("contentflow.api")


def configure_logging() -> None:
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    db.configure_database(settings.database_url)
    configure_logging()
    observability = (
        ObservabilityMetrics(settings, lambda: db.SessionLocal())
        if settings.metrics_enabled
        else None
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if not settings.production:
            upgrade_database(settings)
            db.configure_database(settings.database_url)
            db.create_schema()
        logger.info(
            json.dumps(
                {
                    "event": "app.started",
                    "environment": settings.environment,
                    "database": settings.database_url.split("@")[-1],
                },
                ensure_ascii=False,
            )
        )
        yield

    application = FastAPI(
        title="ContentFlow API",
        version="0.2.0",
        description="AI 内容营销自动化系统 API",
        lifespan=lifespan,
    )
    application.dependency_overrides[get_settings] = lambda: settings
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        if observability is not None:
            observability.request_started(request.method)
        try:
            response = await call_next(request)
        except Exception:
            if observability is not None:
                observability.request_finished(
                    method=request.method,
                    route=request.scope.get("route"),
                    request_path=request.url.path,
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    duration_seconds=time.perf_counter() - started,
                )
            raise
        duration_seconds = time.perf_counter() - started
        duration_ms = round(duration_seconds * 1000, 2)
        if observability is not None:
            observability.request_finished(
                method=request.method,
                route=request.scope.get("route"),
                request_path=request.url.path,
                status_code=response.status_code,
                duration_seconds=duration_seconds,
            )
        response.headers["x-request-id"] = request_id
        response.headers.setdefault("x-content-type-options", "nosniff")
        response.headers.setdefault("x-frame-options", "DENY")
        response.headers.setdefault(
            "referrer-policy", "strict-origin-when-cross-origin"
        )
        response.headers.setdefault(
            "permissions-policy", "camera=(), geolocation=(), microphone=()"
        )
        if request.url.path.startswith(settings.api_prefix):
            response.headers.setdefault("cache-control", "no-store")
        logger.info(
            json.dumps(
                {
                    "event": "http.request",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": duration_ms,
                },
                ensure_ascii=False,
            )
        )
        return response

    @application.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException):
        return JSONResponse(
            status_code=error.status_code,
            headers=error.headers,
            content={
                "error": {
                    "code": f"http_{error.status_code}",
                    "message": error.detail,
                    "request_id": getattr(request.state, "request_id", None),
                }
            },
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "请求参数校验失败",
                    "details": error.errors(),
                    "request_id": getattr(request.state, "request_id", None),
                }
            },
        )

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception):
        request_id = getattr(request.state, "request_id", None)
        logger.error(
            json.dumps(
                {
                    "event": "http.unhandled_error",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "error_type": type(error).__name__,
                },
                ensure_ascii=False,
            ),
            exc_info=(type(error), error, error.__traceback__),
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "Internal server error",
                    "request_id": request_id,
                }
            },
        )

    prefix = settings.api_prefix
    application.include_router(auth.router, prefix=prefix)
    application.include_router(admin.router, prefix=prefix)
    application.include_router(campaigns.router, prefix=prefix)
    application.include_router(runs.router, prefix=prefix)
    application.include_router(knowledge.router, prefix=prefix)
    application.include_router(contents.router, prefix=prefix)
    application.include_router(assets.router, prefix=prefix)
    application.include_router(channels.router, prefix=prefix)
    application.include_router(publishing.router, prefix=prefix)
    application.include_router(metrics.router, prefix=prefix)
    application.include_router(dashboard.router, prefix=prefix)
    application.include_router(jobs.router, prefix=prefix)

    @application.get("/")
    def root():
        return {
            "name": settings.app_name,
            "version": "0.2.0",
            "docs": "/docs",
        }

    @application.get("/health/live")
    def liveness():
        return {"status": "ok"}

    @application.get("/health/ready")
    def readiness():
        with db.SessionLocal() as session:
            session.execute(text("SELECT 1"))
        storage = build_object_storage(settings)
        storage.check()
        return {"status": "ready", "database": "ok", "storage": "ok"}

    @application.get("/metrics", include_in_schema=False)
    def prometheus_metrics(request: Request):
        if observability is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Not found",
                headers={"Cache-Control": "no-store"},
            )
        authorization = request.headers.get("authorization", "")
        scheme, separator, provided_token = authorization.partition(" ")
        expected_token = settings.metrics_bearer_token or ""
        authorized = (
            separator == " "
            and scheme.lower() == "bearer"
            and bool(provided_token)
            and hmac.compare_digest(provided_token, expected_token)
        )
        if not authorized:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Metrics authentication required",
                headers={
                    "WWW-Authenticate": "Bearer",
                    "Cache-Control": "no-store",
                },
            )
        try:
            payload = observability.render()
        except Exception as error:
            logger.error(
                json.dumps(
                    {
                        "event": "metrics.collection_failed",
                        "error_type": type(error).__name__,
                    }
                )
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Metrics collection failed",
                headers={"Cache-Control": "no-store"},
            ) from error
        return Response(
            content=payload,
            headers={
                "Content-Type": CONTENT_TYPE_LATEST,
                "Cache-Control": "no-store",
            },
        )

    return application


app = create_app()
