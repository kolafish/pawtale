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

POSTGRES_DATABASE_URL = os.getenv("TEST_POSTGRES_DATABASE_URL", "").strip()
if POSTGRES_DATABASE_URL:
    os.environ["DATABASE_URL"] = POSTGRES_DATABASE_URL
os.environ["APP_ENV"] = "test"

from app.main import app  # noqa: E402
from app.auth import SESSION_COOKIE_NAME, hash_password, session_token_hash  # noqa: E402
from app.clock import utcnow_naive  # noqa: E402
from app.database import (  # noqa: E402
    Base,
    async_session_maker,
    create_database_engine,
    engine as app_engine,
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


@pytest.fixture(autouse=True)
def skip_narrator_profile_extraction(monkeypatch):
    """Each segment of a first narration queues an AI call for the narrator's profile; other tests don't need it."""
    from app.services.memoir import narrator_profile

    async def skip(*_args, **_kwargs):
        return None

    monkeypatch.setattr(narrator_profile, "schedule_narrator_profile", skip)

    def no_ai(_model=None):
        raise RuntimeError("the narrator profile makes no AI calls in tests")

    # Replies that offer a profile item check whether they asked it; without AI the wording decides.
    monkeypatch.setattr(narrator_profile, "get_ai_service", no_ai)


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
    """Share one pooled engine across API requests and background sessions."""
    # Tests run on the session event loop (pytest.ini), so pooled asyncpg
    # connections stay on the loop that opened them.
    engine = create_async_engine(
        POSTGRES_DATABASE_URL,
        pool_size=5,
        max_overflow=20,
        # A cancelled request can return a closed connection to the pool;
        # replace it on checkout instead of failing the next test's cleanup.
        pool_pre_ping=True,
        # On the shared loop a cancelled request can also leave a connection
        # open in a transaction; let the server end it instead of letting its
        # locks block every later test.
        connect_args={"server_settings": {"idle_in_transaction_session_timeout": "60000"}},
    )
    # Services that open their own sessions must use the same test database.
    async_session_maker.configure(bind=engine)
    try:
        await _clear_postgres_tables(engine)
        yield engine
    finally:
        async_session_maker.configure(bind=app_engine)
        await engine.dispose()


async def _clear_postgres_tables(engine) -> None:
    register_models()
    # DELETE with foreign-key triggers off is far cheaper than TRUNCATE on the
    # nearly empty tables a test leaves behind.
    async with engine.begin() as conn:
        # A transaction a previous test left open would block these deletes,
        # and later schema changes, forever.
        await conn.execute(text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid() "
            "AND state IN ('idle in transaction', 'idle in transaction (aborted)')"
        ))
        await conn.execute(text("SET LOCAL lock_timeout = '30s'"))
        await conn.execute(text("SET LOCAL session_replication_role = replica"))
        raw = await conn.get_raw_connection()
        await raw.driver_connection.execute("; ".join(f'DELETE FROM "{name}"' for name in Base.metadata.tables))


@pytest_asyncio.fixture
async def test_db(test_engine):
    """Give each test real commit boundaries, then clear its database rows."""
    async_session = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    try:
        async with async_session() as session:
            yield session
    finally:
        await _clear_postgres_tables(test_engine)


@pytest_asyncio.fixture
async def anonymous_client(test_db, tmp_path):
    """创建未登录测试客户端（文件存储重定向到临时目录）"""
    async def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db

    try:
        with patch("app.services.content_storage.PROJECTS_DIR", tmp_path / "projects"):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                yield ac
    finally:
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
