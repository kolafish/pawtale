import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations.auth import migrate_auth_schema
from app.models.users import AuthIdentity, User


class TestAuthAPI:
    async def test_register_me_and_logout(self, anonymous_client, test_db):
        register = await anonymous_client.post(
            "/api/auth/register",
            json={
                "email": "author@example.com",
                "password": "password123",
                "display_name": "作者",
            },
        )
        assert register.status_code == 201
        assert register.json()["user"]["email"] == "author@example.com"

        user = await test_db.scalar(select(User).where(User.email == "author@example.com"))
        assert user is not None
        assert user.password_login_enabled is True

        me = await anonymous_client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["display_name"] == "作者"

        logout = await anonymous_client.post("/api/auth/logout")
        assert logout.status_code == 204

        after_logout = await anonymous_client.get("/api/auth/me")
        assert after_logout.status_code == 401

    async def test_project_routes_require_login(self, anonymous_client):
        response = await anonymous_client.get("/api/projects")
        assert response.status_code == 401

    async def test_projects_are_isolated_between_users(self, anonymous_client):
        first_register = await anonymous_client.post(
            "/api/auth/register",
            json={
                "email": "first@example.com",
                "password": "password123",
                "display_name": "第一位作者",
            },
        )
        assert first_register.status_code == 201

        create_project = await anonymous_client.post(
            "/api/projects",
            json={"type": "novel", "title": "第一位作者的项目"},
        )
        assert create_project.status_code == 201
        project_id = create_project.json()["id"]

        await anonymous_client.post("/api/auth/logout")

        second_register = await anonymous_client.post(
            "/api/auth/register",
            json={
                "email": "second@example.com",
                "password": "password123",
                "display_name": "第二位作者",
            },
        )
        assert second_register.status_code == 201

        listing = await anonymous_client.get("/api/projects")
        assert listing.status_code == 200
        assert listing.json() == []

        direct_access = await anonymous_client.get(f"/api/projects/{project_id}")
        assert direct_access.status_code == 404


async def test_auth_identity_provider_user_is_unique(test_db):
    user = User(email="wechat-owner@example.com", password_hash="hash", display_name="Owner")
    other = User(email="wechat-other@example.com", password_hash="hash", display_name="Other")
    test_db.add_all([user, other])
    await test_db.flush()

    test_db.add(
        AuthIdentity(
            user_id=user.id,
            provider="wechat_miniprogram",
            provider_user_id="openid-1",
            provider_union_id="union-1",
        )
    )
    await test_db.commit()

    test_db.add(
        AuthIdentity(
            user_id=other.id,
            provider="wechat_miniprogram",
            provider_user_id="openid-1",
        )
    )
    with pytest.raises(IntegrityError):
        await test_db.commit()
    await test_db.rollback()


async def test_auth_migration_creates_identity_table_and_credentials_flag():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    CREATE TABLE users (
                        id VARCHAR(36) NOT NULL,
                        email VARCHAR(255) NOT NULL,
                        password_hash VARCHAR(255) NOT NULL,
                        display_name VARCHAR(100) NOT NULL,
                        avatar_url VARCHAR(500),
                        created_at DATETIME,
                        updated_at DATETIME,
                        PRIMARY KEY (id)
                    )
                    """
                )
            )
            await conn.execute(text("CREATE UNIQUE INDEX ix_users_email ON users (email)"))

        result = await migrate_auth_schema(engine)
        second_result = await migrate_auth_schema(engine)

        async with engine.begin() as conn:
            user_columns = {
                row[1]
                for row in (
                    await conn.execute(text("PRAGMA table_info(users)"))
                ).fetchall()
            }
            identity_table = await conn.scalar(
                text("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'auth_identities'")
            )

        assert "users.password_login_enabled" in result["changed_columns"]
        assert "auth_identities" in result["changed_tables"]
        assert second_result["changed_columns"] == []
        assert second_result["changed_tables"] == []
        assert "password_login_enabled" in user_columns
        assert identity_table == "auth_identities"
    finally:
        await engine.dispose()
