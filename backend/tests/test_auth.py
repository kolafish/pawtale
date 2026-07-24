from io import BytesIO

import pytest
from PIL import Image
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations.auth import migrate_auth_schema
from app.models.users import AuthIdentity, User
from app.config import Settings
from app.services.wechat_auth import WeChatAuthError, WeChatSession


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

    async def test_register_rejects_invalid_email(self, anonymous_client):
        register = await anonymous_client.post(
            "/api/auth/register",
            json={
                "email": "not-an-email",
                "password": "password123",
            },
        )
        assert register.status_code == 400
        assert register.json()["detail"] == "邮箱格式不正确"

    async def test_project_routes_require_login(self, anonymous_client):
        response = await anonymous_client.get("/api/projects")
        assert response.status_code == 401

    async def test_auth_response_uses_configured_admin_email_role(self, anonymous_client, monkeypatch):
        monkeypatch.setattr(
            "app.auth.get_settings",
            lambda: Settings(admin_emails="config-admin@example.com"),
        )

        register = await anonymous_client.post(
            "/api/auth/register",
            json={
                "email": "config-admin@example.com",
                "password": "password123",
                "display_name": "配置管理员",
            },
        )
        assert register.status_code == 201
        assert register.json()["user"]["role"] == "admin"

        me = await anonymous_client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["role"] == "admin"

    async def test_update_me_display_name(self, anonymous_client, test_db):
        register = await anonymous_client.post(
            "/api/auth/register",
            json={
                "email": "profile@example.com",
                "password": "password123",
                "display_name": "旧昵称",
            },
        )
        assert register.status_code == 201

        update = await anonymous_client.patch("/api/auth/me", json={"display_name": "  新昵称  "})
        assert update.status_code == 200
        assert update.json()["display_name"] == "新昵称"

        user = await test_db.scalar(select(User).where(User.email == "profile@example.com"))
        assert user is not None
        assert user.display_name == "新昵称"

        invalid = await anonymous_client.patch("/api/auth/me", json={"display_name": "   "})
        assert invalid.status_code == 422

        await anonymous_client.post("/api/auth/logout")
        unauthorized = await anonymous_client.patch("/api/auth/me", json={"display_name": "游客"})
        assert unauthorized.status_code == 401

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

    async def test_wechat_login_creates_provisional_user_and_reuses_identity(
        self,
        anonymous_client,
        test_db,
        monkeypatch,
    ):
        calls: list[str] = []

        async def fake_exchange(code: str) -> WeChatSession:
            calls.append(code)
            return WeChatSession(openid="wechat-openid-1", unionid="wechat-unionid-1")

        monkeypatch.setattr("app.routers.auth.exchange_wechat_login_code", fake_exchange)

        first = await anonymous_client.post(
            "/api/auth/wechat-login",
            json={"code": "code-1"},
        )
        assert first.status_code == 200
        first_body = first.json()
        first_user = first_body["user"]
        assert first_body["needs_credentials"] is True
        assert first_user["display_name"] == "微信用户"
        assert first_user["avatar_url"] is None
        assert first_user["email"].startswith("wechat_")
        assert first_user["email"].endswith("@wechat.local")
        assert first_user["password_login_enabled"] is False

        identity = await test_db.scalar(
            select(AuthIdentity).where(AuthIdentity.provider_user_id == "wechat-openid-1")
        )
        assert identity is not None
        assert identity.provider == "wechat_miniprogram"
        assert identity.provider_union_id == "wechat-unionid-1"
        assert identity.user_id == first_user["id"]

        me = await anonymous_client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["id"] == first_user["id"]

        await anonymous_client.post("/api/auth/logout")
        second = await anonymous_client.post(
            "/api/auth/wechat-login",
            json={"code": "code-2"},
        )
        assert second.status_code == 200
        assert second.json()["user"]["id"] == first_user["id"]
        assert second.json()["needs_credentials"] is True
        assert calls == ["code-1", "code-2"]

    async def test_set_credentials_enables_web_login_for_wechat_user(
        self,
        anonymous_client,
        monkeypatch,
    ):
        async def fake_exchange(code: str) -> WeChatSession:
            return WeChatSession(openid=f"openid-{code}")

        monkeypatch.setattr("app.routers.auth.exchange_wechat_login_code", fake_exchange)

        login = await anonymous_client.post("/api/auth/wechat-login", json={"code": "new-user"})
        assert login.status_code == 200
        assert login.json()["needs_credentials"] is True

        credentials = await anonymous_client.post(
            "/api/auth/set-credentials",
            json={
                "email": "wechat-web@example.com",
                "password": "password123",
                "display_name": "微信 Web 用户",
            },
        )
        assert credentials.status_code == 200
        assert credentials.json()["needs_credentials"] is False
        assert credentials.json()["user"]["email"] == "wechat-web@example.com"
        assert credentials.json()["user"]["password_login_enabled"] is True

        await anonymous_client.post("/api/auth/logout")
        web_login = await anonymous_client.post(
            "/api/auth/login",
            json={"email": "wechat-web@example.com", "password": "password123"},
        )
        assert web_login.status_code == 200
        assert web_login.json()["user"]["display_name"] == "微信 Web 用户"

    async def test_wechat_user_can_save_nickname_and_avatar(
        self,
        anonymous_client,
        monkeypatch,
        tmp_path,
    ):
        async def fake_exchange(code: str) -> WeChatSession:
            return WeChatSession(openid=f"openid-{code}")

        monkeypatch.setattr("app.routers.auth.exchange_wechat_login_code", fake_exchange)

        login = await anonymous_client.post(
            "/api/auth/wechat-login",
            json={"code": "profile-user"},
        )
        assert login.status_code == 200
        assert login.json()["user"]["display_name"] == "微信用户"
        assert login.json()["user"]["avatar_url"] is None

        profile = await anonymous_client.patch(
            "/api/auth/me",
            json={"display_name": "微信昵称"},
        )
        assert profile.status_code == 200
        assert profile.json()["display_name"] == "微信昵称"

        monkeypatch.setattr("app.services.user_avatars.DATA_DIR", tmp_path)
        image = Image.new("RGB", (640, 480), color=(166, 75, 62))
        image_bytes = BytesIO()
        image.save(image_bytes, format="PNG")
        avatar = await anonymous_client.post(
            "/api/auth/me/avatar",
            files={"file": ("wechat-avatar.png", image_bytes.getvalue(), "image/png")},
        )
        assert avatar.status_code == 200
        avatar_url = avatar.json()["avatar_url"]
        assert avatar_url.startswith(f"/api/auth/avatars/{login.json()['user']['id']}/")

        public_avatar = await anonymous_client.get(avatar_url)
        assert public_avatar.status_code == 200
        assert public_avatar.headers["content-type"] == "image/jpeg"
        with Image.open(BytesIO(public_avatar.content)) as rendered:
            assert rendered.size == (384, 384)

        replacement = Image.new("RGB", (480, 640), color=(59, 101, 82))
        replacement_bytes = BytesIO()
        replacement.save(replacement_bytes, format="JPEG")
        replaced_avatar = await anonymous_client.post(
            "/api/auth/me/avatar",
            files={"file": ("wechat-avatar.jpg", replacement_bytes.getvalue(), "image/jpeg")},
        )
        assert replaced_avatar.status_code == 200
        assert replaced_avatar.json()["avatar_url"] != avatar_url
        assert (await anonymous_client.get(avatar_url)).status_code == 404
        assert (
            await anonymous_client.get(replaced_avatar.json()["avatar_url"])
        ).status_code == 200

    async def test_avatar_upload_rejects_non_image(self, anonymous_client, tmp_path, monkeypatch):
        register = await anonymous_client.post(
            "/api/auth/register",
            json={"email": "avatar@example.com", "password": "password123"},
        )
        assert register.status_code == 201
        monkeypatch.setattr("app.services.user_avatars.DATA_DIR", tmp_path)

        avatar = await anonymous_client.post(
            "/api/auth/me/avatar",
            files={"file": ("avatar.txt", b"not-an-image", "text/plain")},
        )

        assert avatar.status_code == 400
        assert avatar.json()["detail"] == "仅支持有效的头像图片"

    async def test_avatar_route_rejects_non_uuid_user_path(self, anonymous_client):
        avatar = await anonymous_client.get(
            "/api/auth/avatars/not-a-user/0123456789abcdef0123456789abcdef.jpg",
        )

        assert avatar.status_code == 404
        assert avatar.json()["detail"] == "头像不存在"

    async def test_set_credentials_rejects_invalid_email(self, anonymous_client, monkeypatch):
        async def fake_exchange(code: str) -> WeChatSession:
            return WeChatSession(openid=f"openid-{code}")

        monkeypatch.setattr("app.routers.auth.exchange_wechat_login_code", fake_exchange)

        login = await anonymous_client.post("/api/auth/wechat-login", json={"code": "bad-email"})
        assert login.status_code == 200

        credentials = await anonymous_client.post(
            "/api/auth/set-credentials",
            json={
                "email": "wechat-profile",
                "password": "password123",
            },
        )
        assert credentials.status_code == 400
        assert credentials.json()["detail"] == "邮箱格式不正确"

    async def test_set_credentials_replaces_wechat_placeholder_with_email_name(
        self,
        anonymous_client,
        monkeypatch,
    ):
        async def fake_exchange(code: str) -> WeChatSession:
            return WeChatSession(openid=f"openid-{code}")

        monkeypatch.setattr("app.routers.auth.exchange_wechat_login_code", fake_exchange)

        login = await anonymous_client.post("/api/auth/wechat-login", json={"code": "placeholder-user"})
        assert login.status_code == 200
        assert login.json()["user"]["display_name"] == "微信用户"

        credentials = await anonymous_client.post(
            "/api/auth/set-credentials",
            json={
                "email": "memoir-author@example.com",
                "password": "password123",
            },
        )
        assert credentials.status_code == 200
        assert credentials.json()["needs_credentials"] is False
        assert credentials.json()["user"]["display_name"] == "memoir-author"

    async def test_wechat_bind_links_existing_password_user(self, anonymous_client, monkeypatch):
        async def fake_exchange(code: str) -> WeChatSession:
            return WeChatSession(openid="bind-openid", unionid="bind-unionid")

        monkeypatch.setattr("app.routers.auth.exchange_wechat_login_code", fake_exchange)

        register = await anonymous_client.post(
            "/api/auth/register",
            json={
                "email": "bound@example.com",
                "password": "password123",
                "display_name": "已有用户",
            },
        )
        assert register.status_code == 201
        user_id = register.json()["user"]["id"]

        bind = await anonymous_client.post("/api/auth/wechat-bind", json={"code": "bind-code"})
        assert bind.status_code == 200
        assert bind.json()["needs_credentials"] is False

        await anonymous_client.post("/api/auth/logout")
        wechat_login = await anonymous_client.post(
            "/api/auth/wechat-login",
            json={"code": "bind-code"},
        )
        assert wechat_login.status_code == 200
        assert wechat_login.json()["user"]["id"] == user_id
        assert wechat_login.json()["needs_credentials"] is False

    async def test_set_credentials_rejects_existing_password_user(self, anonymous_client):
        register = await anonymous_client.post(
            "/api/auth/register",
            json={"email": "ready@example.com", "password": "password123"},
        )
        assert register.status_code == 201

        response = await anonymous_client.post(
            "/api/auth/set-credentials",
            json={"email": "new-ready@example.com", "password": "password456"},
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "账号已设置登录凭据"

    async def test_wechat_bind_rejects_identity_bound_to_another_user(
        self,
        anonymous_client,
        monkeypatch,
    ):
        async def fake_exchange(code: str) -> WeChatSession:
            return WeChatSession(openid="shared-openid")

        monkeypatch.setattr("app.routers.auth.exchange_wechat_login_code", fake_exchange)

        first = await anonymous_client.post(
            "/api/auth/register",
            json={"email": "first-bind@example.com", "password": "password123"},
        )
        assert first.status_code == 201
        first_bind = await anonymous_client.post("/api/auth/wechat-bind", json={"code": "same"})
        assert first_bind.status_code == 200

        await anonymous_client.post("/api/auth/logout")
        second = await anonymous_client.post(
            "/api/auth/register",
            json={"email": "second-bind@example.com", "password": "password123"},
        )
        assert second.status_code == 201
        conflict = await anonymous_client.post("/api/auth/wechat-bind", json={"code": "same"})
        assert conflict.status_code == 409
        assert conflict.json()["detail"] == "该微信已绑定其他账号"

    async def test_wechat_login_rejects_empty_code(self, anonymous_client):
        response = await anonymous_client.post("/api/auth/wechat-login", json={"code": " "})
        assert response.status_code == 400
        assert response.json()["detail"] == "缺少微信登录 code"

    async def test_wechat_login_reports_provider_error(self, anonymous_client, monkeypatch):
        async def fake_exchange(code: str) -> WeChatSession:
            raise WeChatAuthError("微信小程序登录未配置")

        monkeypatch.setattr("app.routers.auth.exchange_wechat_login_code", fake_exchange)

        response = await anonymous_client.post("/api/auth/wechat-login", json={"code": "bad"})
        assert response.status_code == 502
        assert response.json()["detail"] == "微信小程序登录未配置"


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
        assert "users.role" in result["changed_columns"]
        assert "auth_identities" in result["changed_tables"]
        assert second_result["changed_columns"] == []
        assert second_result["changed_tables"] == []
        assert "password_login_enabled" in user_columns
        assert "role" in user_columns
        assert identity_table == "auth_identities"
    finally:
        await engine.dispose()
