import pytest
import pytest_asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.main import app
from app.database import Base, get_db, register_models
from app.migrations.memoir_voice import migrate_memoir_voice_schema


# 使用内存数据库进行测试
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def test_db():
    """创建测试数据库"""
    register_models()
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await migrate_memoir_voice_schema(engine)

    async with async_session() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

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
