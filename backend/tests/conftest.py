import pytest_asyncio
from unittest.mock import patch
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.main import app
from app.database import Base, create_database_engine, get_db, register_models, settings
from app.migrations.audit import migrate_audit_schema
from app.migrations.memoir_client_snapshots import migrate_memoir_client_snapshot_schema
from app.migrations.memoir_images import migrate_memoir_image_schema
from app.migrations.memoir_narrative import migrate_memoir_narrative_schema
from app.migrations.memoir_voice import migrate_memoir_voice_schema


@pytest_asyncio.fixture
async def test_db(tmp_path):
    """创建测试数据库"""
    register_models()
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    test_settings = settings.model_copy(
        update={"app_env": "test", "database_url": database_url}
    )
    engine = create_database_engine(test_settings)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        foreign_keys = await conn.exec_driver_sql("PRAGMA foreign_keys")
        assert foreign_keys.scalar_one() == 1
        await conn.run_sync(Base.metadata.create_all)
    await migrate_memoir_voice_schema(engine)
    await migrate_memoir_image_schema(engine)
    await migrate_memoir_narrative_schema(engine)
    await migrate_memoir_client_snapshot_schema(engine)
    await migrate_audit_schema(engine)

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
