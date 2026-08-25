import os
from pathlib import Path

import pytest
import pytest_asyncio
from unittest.mock import patch
from alembic import command
from alembic.config import Config
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

POSTGRES_DATABASE_URL = os.getenv("TEST_POSTGRES_DATABASE_URL", "").strip()
if not POSTGRES_DATABASE_URL:
    raise RuntimeError("TEST_POSTGRES_DATABASE_URL is required for backend tests")
os.environ["DATABASE_URL"] = POSTGRES_DATABASE_URL
os.environ["APP_ENV"] = "test"

from app.main import app  # noqa: E402
from app.database import (  # noqa: E402
    Base,
    create_database_engine,
    get_db,
    register_models,
    settings,
)
from app.migrations.postgres_runtime import (  # noqa: E402
    assert_postgres_runtime_schema,
)


def _assert_dedicated_postgres_test_database() -> None:
    database_name = (make_url(POSTGRES_DATABASE_URL).database or "").lower()
    if not (database_name.startswith("test_") or database_name.endswith("_test")):
        raise RuntimeError(
            "TEST_POSTGRES_DATABASE_URL must name a dedicated test database "
            "starting with test_ or ending with _test"
        )


def _postgres_alembic_config() -> Config:
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", POSTGRES_DATABASE_URL)
    return config


@pytest.fixture(scope="session", autouse=True)
def postgres_schema():
    _assert_dedicated_postgres_test_database()
    command.upgrade(_postgres_alembic_config(), "head")
    yield


async def _truncate_postgres_tables(engine) -> None:
    register_models()
    table_names = ", ".join(f'"{name}"' for name in Base.metadata.tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))


@pytest_asyncio.fixture
async def test_db():
    """创建隔离的 PostgreSQL 测试会话。"""
    register_models()
    test_settings = settings.model_copy(
        update={"app_env": "test", "database_url": POSTGRES_DATABASE_URL}
    )
    engine = create_database_engine(test_settings)
    async_session = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    await assert_postgres_runtime_schema(engine)
    await _truncate_postgres_tables(engine)
    async with async_session() as session:
        yield session
    await _truncate_postgres_tables(engine)
    await engine.dispose()


@pytest_asyncio.fixture
async def anonymous_client(test_db, tmp_path):
    """创建未登录测试客户端（文件存储重定向到临时目录）"""
    async def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db

    with patch("app.services.content_storage.PROJECTS_DIR", tmp_path / "projects"):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(anonymous_client):
    """创建已登录测试客户端，保持既有业务测试聚焦业务行为。"""
    response = await anonymous_client.post(
        "/api/auth/register",
        json={
            "email": "tester@example.com",
            "password": "password123",
            "display_name": "测试用户",
        },
    )
    assert response.status_code == 201
    yield anonymous_client
