from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import Settings, get_settings


SQLITE_JOURNAL_MODES = {"DELETE", "TRUNCATE", "PERSIST", "MEMORY", "WAL", "OFF"}
SQLITE_SYNCHRONOUS_MODES = {"OFF", "NORMAL", "FULL", "EXTRA", "0", "1", "2", "3"}

settings = get_settings()


def is_sqlite_database_url(database_url: str) -> bool:
    return database_url.startswith("sqlite")


def sqlite_engine_connect_args(current_settings: Settings) -> dict[str, float]:
    if not is_sqlite_database_url(current_settings.database_url):
        return {}
    timeout_ms = max(0, int(current_settings.sqlite_busy_timeout_ms))
    return {"timeout": timeout_ms / 1000}


def _sqlite_pragma_value(value: str, allowed: set[str], setting_name: str) -> str:
    normalized = value.strip().upper()
    if normalized not in allowed:
        valid_values = ", ".join(sorted(allowed))
        raise RuntimeError(f"{setting_name} must be one of: {valid_values}")
    return normalized


def configure_sqlite_connection(dbapi_connection: Any, current_settings: Settings) -> None:
    busy_timeout_ms = max(0, int(current_settings.sqlite_busy_timeout_ms))
    journal_mode = _sqlite_pragma_value(
        current_settings.sqlite_journal_mode,
        SQLITE_JOURNAL_MODES,
        "SQLITE_JOURNAL_MODE",
    )
    synchronous = _sqlite_pragma_value(
        current_settings.sqlite_synchronous,
        SQLITE_SYNCHRONOUS_MODES,
        "SQLITE_SYNCHRONOUS",
    )

    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
        cursor.execute(f"PRAGMA journal_mode = {journal_mode}")
        cursor.execute(f"PRAGMA synchronous = {synchronous}")
    finally:
        cursor.close()


def create_database_engine(current_settings: Settings) -> AsyncEngine:
    engine = create_async_engine(
        current_settings.database_url,
        echo=current_settings.app_env == "development",
        connect_args=sqlite_engine_connect_args(current_settings),
    )
    if is_sqlite_database_url(current_settings.database_url):
        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
            configure_sqlite_connection(dbapi_connection, current_settings)

    return engine


engine = create_database_engine(settings)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


def register_models() -> None:
    import app.models  # noqa: F401


async def get_db():
    async with async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    register_models()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
