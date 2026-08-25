from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import Settings, get_settings


settings = get_settings()


def create_database_engine(current_settings: Settings) -> AsyncEngine:
    if make_url(current_settings.database_url).drivername != "postgresql+asyncpg":
        raise RuntimeError("DATABASE_URL must use postgresql+asyncpg")
    return create_async_engine(
        current_settings.database_url,
        echo=current_settings.app_env == "development",
        pool_pre_ping=True,
        pool_size=max(1, current_settings.database_pool_size),
        max_overflow=max(0, current_settings.database_max_overflow),
        pool_timeout=max(0.1, current_settings.database_pool_timeout_seconds),
        pool_recycle=max(0, current_settings.database_pool_recycle_seconds),
    )


engine = create_database_engine(settings)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@asynccontextmanager
async def commit_or_rollback(session: AsyncSession) -> AsyncIterator[None]:
    """Own one request-level unit of work on an existing session."""
    try:
        yield
        await session.commit()
    except BaseException:
        await session.rollback()
        raise


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
