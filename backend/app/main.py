import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from app.application_errors import (
    ApplicationError,
    InvalidRequestError,
    ResourceNotFoundError,
)
from app.config import get_settings, validate_runtime_settings
from app.database import engine
from app.migrations.postgres_runtime import (
    assert_postgres_runtime_schema as assert_runtime_schema,
)
from app.routers import audit_router, auth_router, github_router, memoir_router, projects_router
from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
from app.services.memoir.editing_jobs import editing_worker_loop
from app.services.memoir.share_video_jobs import share_video_worker_loop
from app.services.ai_factory import UnsupportedAIModelError


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_runtime_settings()
    await assert_runtime_schema(engine)
    register_memoir_editing_job_handlers()
    settings = get_settings()
    stop_event: asyncio.Event | None = None
    worker_tasks: list[asyncio.Task] = []
    if settings.memoir_editing_worker_enabled or settings.memoir_share_video_worker_enabled:
        stop_event = asyncio.Event()
    if stop_event and settings.memoir_editing_worker_enabled:
        worker_tasks.append(asyncio.create_task(editing_worker_loop(stop_event)))
    if stop_event and settings.memoir_share_video_worker_enabled:
        worker_tasks.append(asyncio.create_task(share_video_worker_loop(stop_event)))
    try:
        yield
    finally:
        if stop_event and worker_tasks:
            stop_event.set()
            await asyncio.gather(*worker_tasks, return_exceptions=True)


app = FastAPI(
    title="Pawtale API",
    description="是我的CP呀 - AI故事创作平台",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(UnsupportedAIModelError)
async def unsupported_ai_model_handler(_request, exc: UnsupportedAIModelError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(ApplicationError)
async def application_error_handler(_request, exc: ApplicationError):
    status_code = {
        InvalidRequestError: 400,
        ResourceNotFoundError: 404,
    }[type(exc)]
    return JSONResponse(status_code=status_code, content={"detail": str(exc)})


# 注册路由
app.include_router(auth_router)
app.include_router(audit_router)
app.include_router(github_router)
app.include_router(memoir_router)
app.include_router(projects_router)


@app.get("/")
async def root():
    return {
        "name": "Pawtale API",
        "slogan": "是我的CP呀",
        "version": "0.1.0",
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
