from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import db
from .migrate import upgrade_database
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
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers["x-request-id"] = request_id
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
        return {"status": "ready"}

    return application


app = create_app()
