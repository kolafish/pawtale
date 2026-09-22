import os
import secrets
from datetime import timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from unittest.mock import patch
from alembic import command
from alembic.config import Config
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

POSTGRES_DATABASE_URL = os.getenv("TEST_POSTGRES_DATABASE_URL", "").strip()
if POSTGRES_DATABASE_URL:
    os.environ["DATABASE_URL"] = POSTGRES_DATABASE_URL
os.environ["APP_ENV"] = "test"

from app.main import app  # noqa: E402
from app.auth import SESSION_COOKIE_NAME, hash_password, session_token_hash  # noqa: E402
from app.clock import utcnow_naive  # noqa: E402
from app.database import (  # noqa: E402
    Base,
    create_database_engine,
    get_db,
    register_models,
    settings,
)
from app.models.users import AuthSession, User  # noqa: E402
from app.migrations.postgres_runtime import (  # noqa: E402
    assert_postgres_runtime_schema,
)


def pytest_addoption(parser):
    parser.addoption("--ci-fast", action="store_true", help="run fast CI tests and marked database smoke tests")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--ci-fast"):
        return
    selected = []
    deselected = []
    for item in items:
        uses_database = "test_db" in item.fixturenames or "postgres_schema" in item.fixturenames
        if uses_database and item.get_closest_marker("ci_smoke") is None:
            deselected.append(item)
        else:
            selected.append(item)
    config.hook.pytest_deselected(items=deselected)
    items[:] = selected


def _assert_dedicated_postgres_test_database() -> None:
    if not POSTGRES_DATABASE_URL:
        raise RuntimeError("TEST_POSTGRES_DATABASE_URL is required for database/API tests")
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


@pytest.fixture(scope="session")
def postgres_schema():
    _assert_dedicated_postgres_test_database()
    command.upgrade(_postgres_alembic_config(), "head")
    yield


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def verified_postgres_schema(postgres_schema):
    """Verify the migrated runtime schema once for the whole test session."""
    test_settings = settings.model_copy(
        update={"app_env": "test", "database_url": POSTGRES_DATABASE_URL}
    )
    engine = create_database_engine(test_settings)
    try:
        await assert_postgres_runtime_schema(engine)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def test_engine(verified_postgres_schema):
    """Share a loop-safe engine while keeping each test's data isolated by transaction."""
    # NullPool avoids reusing asyncpg connections across pytest-asyncio's per-test loops.
    engine = create_async_engine(
        POSTGRES_DATABASE_URL,
        poolclass=NullPool,
        pool_pre_ping=True,
    )
    await _truncate_postgres_tables(engine)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _truncate_postgres_tables(engine) -> None:
    register_models()
    table_names = ", ".join(f'"{name}"' for name in Base.metadata.tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))


@pytest_asyncio.fixture
async def test_db(request, test_engine):
    """Run ordinary tests in a rollback-only transaction; isolate worker tests with truncation."""
    if (
        request.node.get_closest_marker("db_independent") is not None
        or request.node.get_closest_marker("db_transaction_boundary") is not None
    ):
        async_session = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with async_session() as session:
            yield session
        await _truncate_postgres_tables(test_engine)
        return

    connection = await test_engine.connect()
    transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()


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


@pytest.fixture(scope="session")
def test_user_password_hash():
    """Calculate the test password once; authentication endpoints test hashing separately."""
    return hash_password("password123")


@pytest_asyncio.fixture
async def client(anonymous_client, test_db, test_user_password_hash):
    """创建已登录测试客户端，保持既有业务测试聚焦业务行为。"""
    # Registration and password hashing have dedicated auth tests. Most cases
    # only need an authenticated user, so create that state directly.
    token = secrets.token_urlsafe(32)
    user = User(
        email="tester@example.com",
        password_hash=test_user_password_hash,
        display_name="测试用户",
    )
    test_db.add(user)
    await test_db.flush()
    test_db.add(
        AuthSession(
            token_hash=session_token_hash(token),
            user_id=user.id,
            expires_at=utcnow_naive() + timedelta(days=30),
        )
    )
    await test_db.commit()
    anonymous_client.cookies.set(SESSION_COOKIE_NAME, token)
    yield anonymous_client


@pytest_asyncio.fixture
async def admin_client(client, test_db):
    """Promote the fixture user for workflows available only to administrators."""
    response = await client.get("/api/auth/me")
    user = await test_db.get(User, response.json()["id"])
    assert user is not None
    user.role = "admin"
    await test_db.commit()
    return client
