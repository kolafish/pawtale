import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from app.config import get_settings, validate_runtime_settings
from app.database import init_db, engine
from app.migrations.auth import migrate_auth_schema
from app.migrations.git_sync import migrate_git_sync_schema
from app.migrations.memoir_semantic import migrate_memoir_semantic_schema
from app.migrations.memoir_voice import assert_memoir_voice_schema, migrate_memoir_voice_schema
from app.routers import auth_router, github_router, memoir_router, projects_router
from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
from app.services.memoir.editing_jobs import editing_worker_loop


async def _auto_migrate():
    """Add missing columns to existing tables."""
    async with engine.begin() as conn:
        # Add word_count column to documents if missing
        result = await conn.execute(text("PRAGMA table_info(documents)"))
        doc_columns = {row[1] for row in result.fetchall()}
        if "word_count" not in doc_columns:
            await conn.execute(text("ALTER TABLE documents ADD COLUMN word_count INTEGER DEFAULT 0"))
        result = await conn.execute(text("PRAGMA table_info(projects)"))
        project_columns = {row[1] for row in result.fetchall()}
        if "type" not in project_columns:
            await conn.execute(text("ALTER TABLE projects ADD COLUMN type VARCHAR(20) NOT NULL DEFAULT 'novel'"))
        if "metadata_json" not in project_columns:
            await conn.execute(text("ALTER TABLE projects ADD COLUMN metadata_json JSON NOT NULL DEFAULT '{}'"))
        result = await conn.execute(text("PRAGMA table_info(memoir_book_plan_chapters)"))
        book_chapter_columns = {row[1] for row in result.fetchall()}
        if "summary" in book_chapter_columns:
            await conn.execute(text("ALTER TABLE memoir_book_plan_chapters DROP COLUMN summary"))
        result = await conn.execute(text("PRAGMA table_info(memoir_book_output_versions)"))
        book_output_version_columns = {row[1] for row in result.fetchall()}
        if "manuscript_json" not in book_output_version_columns:
            await conn.execute(
                text("ALTER TABLE memoir_book_output_versions ADD COLUMN manuscript_json JSON NOT NULL DEFAULT '{}'")
            )
    await migrate_auth_schema(engine)
    await migrate_git_sync_schema(engine)
    await migrate_memoir_voice_schema(engine)
    await migrate_memoir_semantic_schema(engine)
    await assert_memoir_voice_schema(engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化数据库
    validate_runtime_settings()
    await init_db()
    await _auto_migrate()
    register_memoir_editing_job_handlers()
    settings = get_settings()
    stop_event: asyncio.Event | None = None
    worker_tasks: list[asyncio.Task] = []
    if settings.memoir_editing_worker_enabled:
        stop_event = asyncio.Event()
    if stop_event and settings.memoir_editing_worker_enabled:
        worker_tasks.append(asyncio.create_task(editing_worker_loop(stop_event)))
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

# 注册路由
app.include_router(auth_router)
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
