"""FastAPI 应用入口。"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import models as _models  # noqa: F401  确保模型完成注册
from app.api.v1.router import api_router
from app.core import lifecycle
from app.core import redis as redis_client
from app.core.config import DEFAULT_JWT_SECRET, settings
from app.core.database import dispose_read_engine, engine
from app.core.exceptions import AppError
from app.core.logging import setup_logging
from app.core.middleware import RequestIDMiddleware, SecurityHeadersMiddleware
from app.core.openapi import API_DESCRIPTION, TAGS_METADATA
from app.core.redis import get_redis
from app.workers.queue import close_pool

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    if settings.APP_ENV.lower() != "production" and settings.JWT_SECRET_KEY == DEFAULT_JWT_SECRET:
        logger.warning("开发环境正在使用默认 JWT 密钥，请勿将服务暴露到非可信网络")
    lifecycle.reset_shutdown_state()
    yield
    lifecycle.begin_shutdown()
    cancelled = await lifecycle.drain_generations(settings.SHUTDOWN_DRAIN_SECONDS)
    if cancelled:
        logger.warning("停机时取消了 %s 个在途生成任务", cancelled)
    await close_pool()
    await redis_client.close_redis()
    await dispose_read_engine()
    await engine.dispose()


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": {"code": exc.code, "message": exc.message}},
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "detail": {
                    "code": "INVALID_REQUEST",
                    "message": "请求参数校验失败",
                    "errors": jsonable_encoder(exc.errors()),
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("未处理的异常: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"detail": {"code": "INTERNAL_ERROR", "message": "服务器内部错误"}},
        )


def _configure_openapi(app: FastAPI) -> None:
    """移除框架默认 422，统一由 400 INVALID_REQUEST 响应参数校验错误。"""

    def custom_openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema

        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
            tags=TAGS_METADATA,
        )
        for path_item in schema.get("paths", {}).values():
            for operation in path_item.values():
                if isinstance(operation, dict):
                    operation.get("responses", {}).pop("422", None)
        components = schema.get("components", {}).get("schemas", {})
        components.pop("HTTPValidationError", None)
        components.pop("ValidationError", None)
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version="0.3.0",
        description=API_DESCRIPTION,
        openapi_tags=TAGS_METADATA,
        debug=settings.DEBUG,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts_list)
    app.add_middleware(SecurityHeadersMiddleware)
    register_exception_handlers(app)
    app.add_middleware(RequestIDMiddleware)
    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    @app.get("/health", tags=["system"])
    async def health() -> dict:
        return {"status": "ok", "app": settings.APP_NAME, "env": settings.APP_ENV}

    @app.get("/health/live", tags=["system"])
    async def health_live() -> dict:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["system"])
    async def health_ready() -> JSONResponse:
        db_ok = True
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception:
            db_ok = False

        redis_ok = True
        try:
            await get_redis().ping()
        except Exception:
            redis_ok = False

        if not (db_ok and redis_ok):
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "db": db_ok, "redis": redis_ok},
            )
        return JSONResponse(
            status_code=200, content={"status": "ok", "db": db_ok, "redis": redis_ok}
        )

    _configure_openapi(app)
    return app


app = create_app()
