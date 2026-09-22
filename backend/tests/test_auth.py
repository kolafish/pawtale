import asyncio
from datetime import timedelta
from io import BytesIO

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.auth import SESSION_COOKIE_NAME, session_token_hash
from app.clock import utcnow_naive
from app.database import get_db
from app.main import app
from app.models.users import AuthIdentity, AuthSession, User
from app.routers.websocket_auth import authenticate_websocket
from app.config import Settings
from app.services.wechat_auth import WeChatAuthError, WeChatSession


class TestAuthAPI:
    async def test_logout_revokes_only_the_presented_session(self, anonymous_client, test_db):
        register = await anonymous_client.post(
            "/api/auth/register",
            json={"email": "sessions@example.com", "password": "password123"},
        )
        assert register.status_code == 201
        first_token = anonymous_client.cookies[SESSION_COOKIE_NAME]
        assert register.json()["user"]["id"] not in first_token
        stored = await test_db.get(AuthSession, session_token_hash(first_token))
        assert stored is not None
        assert stored.token_hash != first_token

        login = await anonymous_client.post(
            "/api/auth/login",
            json={"email": "sessions@example.com", "password": "password123"},
        )
        assert login.status_code == 200
        second_token = anonymous_client.cookies[SESSION_COOKIE_NAME]
        assert second_token != first_token
        assert (await anonymous_client.post("/api/auth/logout")).status_code == 204

        anonymous_client.cookies.set(SESSION_COOKIE_NAME, second_token)
        assert (await anonymous_client.get("/api/auth/me")).status_code == 401
        anonymous_client.cookies.set(SESSION_COOKIE_NAME, first_token)
        assert (await anonymous_client.get("/api/auth/me")).status_code == 200

    async def test_expired_session_is_rejected(self, anonymous_client, test_db):
        assert (await anonymous_client.post(
            "/api/auth/register",
            json={"email": "expired@example.com", "password": "password123"},
        )).status_code == 201
        token = anonymous_client.cookies[SESSION_COOKIE_NAME]
        session = await test_db.get(AuthSession, session_token_hash(token))
        session.expires_at = utcnow_naive() - timedelta(seconds=1)
        await test_db.commit()
        assert (await anonymous_client.get("/api/auth/me")).status_code == 401

    @pytest.mark.db_independent
    async def test_websocket_auth_rejects_revoked_session(self, anonymous_client, test_db):
        assert (await anonymous_client.post(
            "/api/auth/register",
            json={"email": "socket@example.com", "password": "password123"},
        )).status_code == 201
        token = anonymous_client.cookies[SESSION_COOKIE_NAME]
        socket = type("Socket", (), {"cookies": {SESSION_COOKIE_NAME: token}})()
        independent_sessions = async_sessionmaker(test_db.bind, expire_on_commit=False)
        async with independent_sessions() as db:
            assert await authenticate_websocket(socket, db) is not None
        await anonymous_client.post("/api/auth/logout")
        async with independent_sessions() as db:
            assert await authenticate_websocket(socket, db) is None

    async def test_wechat_login_attempt_limit(self, anonymous_client, monkeypatch):
        monkeypatch.setattr("app.auth.WECHAT_LOGIN_ATTEMPT_LIMIT", 2)

        async def exchange(code, _miniprogram):
            return WeChatSession(openid=f"limited-{code}")

        monkeypatch.setattr("app.routers.auth.exchange_wechat_login_code", exchange)
        assert (await anonymous_client.post("/api/auth/wechat-login", json={"code": "one"})).status_code == 200
        assert (await anonymous_client.post("/api/auth/wechat-login", json={"code": "two"})).status_code == 200
        assert (await anonymous_client.post("/api/auth/wechat-login", json={"code": "three"})).status_code == 429

    @pytest.mark.db_independent
    async def test_concurrent_first_wechat_login_uses_one_identity(
        self, anonymous_client, test_db, monkeypatch,
    ):
        async def exchange(_code, _miniprogram):
            await asyncio.sleep(0.02)
            return WeChatSession(openid="concurrent-first-login")

        independent_sessions = async_sessionmaker(test_db.bind, expire_on_commit=False)

        async def independent_db():
            async with independent_sessions() as db:
                yield db

        monkeypatch.setattr("app.routers.auth.exchange_wechat_login_code", exchange)
        app.dependency_overrides[get_db] = independent_db
        try:
            first, second = await asyncio.gather(
                anonymous_client.post("/api/auth/wechat-login", json={"code": "first"}),
                anonymous_client.post("/api/auth/wechat-login", json={"code": "second"}),
            )
        finally:
            app.dependency_overrides.pop(get_db, None)
        assert first.status_code == second.status_code == 200
        assert first.json()["user"]["id"] == second.json()["user"]["id"]
        identities = (
            await test_db.scalars(
                select(AuthIdentity).where(AuthIdentity.provider_user_id == "concurrent-first-login")
            )
        ).all()
        assert len(identities) == 1

    @pytest.mark.ci_smoke
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

    async def test_production_closes_email_account_routes(self, anonymous_client, monkeypatch):
        monkeypatch.setattr("app.routers.auth.is_production_environment", lambda: True)
        for route, payload in (
            ("/register", {"email": "author@example.com", "password": "password123"}),
            ("/login", {"email": "author@example.com", "password": "password123"}),
        ):
            response = await anonymous_client.post(f"/api/auth{route}", json=payload)
            assert response.status_code == 404

        monkeypatch.setattr("app.routers.auth.is_production_environment", lambda: False)

        async def exchange(_code, _miniprogram):
            return WeChatSession(openid="wechat-only-production-test")

        monkeypatch.setattr("app.routers.auth.exchange_wechat_login_code", exchange)
        assert (await anonymous_client.post("/api/auth/wechat-login", json={"code": "valid"})).status_code == 200
        monkeypatch.setattr("app.routers.auth.is_production_environment", lambda: True)
        for route, payload in (
            ("/set-credentials", {"email": "author@example.com", "password": "password123"}),
            ("/wechat-bind", {"code": "valid"}),
        ):
            response = await anonymous_client.post(f"/api/auth{route}", json=payload)
            assert response.status_code == 404

    async def test_project_routes_require_login(self, anonymous_client):
        response = await anonymous_client.get("/api/projects")
        assert response.status_code == 401

    @pytest.mark.parametrize("via_wechat", [False, True])
    async def test_unverified_email_cannot_grant_admin_role(
        self, anonymous_client, test_db, monkeypatch, via_wechat,
    ):
        monkeypatch.setenv("ADMIN_EMAILS", "config-admin@example.com")
        monkeypatch.setattr(
            "app.auth.get_settings",
            lambda: Settings(_env_file=None),
        )
        if via_wechat:
            async def exchange(_code, _miniprogram):
                return WeChatSession(openid="admin-email-regression")

            monkeypatch.setattr("app.routers.auth.exchange_wechat_login_code", exchange)
            login = await anonymous_client.post("/api/auth/wechat-login", json={"code": "test"})
            assert login.status_code == 200

        register = await anonymous_client.post(
            "/api/auth/set-credentials" if via_wechat else "/api/auth/register",
            json={
                "email": "config-admin@example.com",
                "password": "password123",
                "display_name": "配置管理员",
            },
        )
        assert register.status_code == (200 if via_wechat else 201)
        assert register.json()["user"]["role"] == "user"

        me = await anonymous_client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["role"] == "user"
        dashboard = "/api/audit/dashboard?from=2026-09-01&to=2026-09-07"
        assert (await anonymous_client.get(dashboard)).status_code == 403

        user = await test_db.get(User, register.json()["user"]["id"])
        assert user.role == "user"
        user.role = "admin"
        await test_db.commit()
        me = await anonymous_client.get("/api/auth/me")
        assert me.json()["role"] == "admin"
        assert (await anonymous_client.get(dashboard)).status_code == 200

        user.role = "user"
        await test_db.commit()
        assert (await anonymous_client.get(dashboard)).status_code == 403

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

        async def fake_exchange(code: str, _miniprogram: str) -> WeChatSession:
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

    async def test_wechat_login_reuses_union_user_across_miniprograms(
        self,
        anonymous_client,
        test_db,
        monkeypatch,
    ):
        calls: list[tuple[str, str]] = []

        async def fake_exchange(code: str, miniprogram: str) -> WeChatSession:
            calls.append((code, miniprogram))
            return WeChatSession(
                openid=f"{miniprogram}-openid",
                unionid="shared-unionid",
            )

        monkeypatch.setattr("app.routers.auth.exchange_wechat_login_code", fake_exchange)

        laoyu = await anonymous_client.post(
            "/api/auth/wechat-login",
            json={"code": "laoyu-code"},
        )
        await anonymous_client.post("/api/auth/logout")
        xiaoyu = await anonymous_client.post(
            "/api/auth/wechat-login",
            json={"code": "xiaoyu-code", "miniprogram": "xiaoyu"},
        )

        assert laoyu.status_code == 200
        assert xiaoyu.status_code == 200
        assert xiaoyu.json()["user"]["id"] == laoyu.json()["user"]["id"]
        identities = (
            await test_db.scalars(
                select(AuthIdentity).where(
                    AuthIdentity.provider_union_id == "shared-unionid"
                )
            )
        ).all()
        assert {identity.provider for identity in identities} == {
            "wechat_miniprogram",
            "wechat_miniprogram_xiaoyu",
        }
        assert calls == [
            ("laoyu-code", "laoyu"),
            ("xiaoyu-code", "xiaoyu"),
        ]

    async def test_wechat_openid_is_scoped_to_miniprogram(
        self,
        anonymous_client,
        monkeypatch,
    ):
        async def fake_exchange(_code: str, _miniprogram: str) -> WeChatSession:
            return WeChatSession(openid="same-openid")

        monkeypatch.setattr("app.routers.auth.exchange_wechat_login_code", fake_exchange)

        laoyu = await anonymous_client.post(
            "/api/auth/wechat-login",
            json={"code": "laoyu-code"},
        )
        await anonymous_client.post("/api/auth/logout")
        xiaoyu = await anonymous_client.post(
            "/api/auth/wechat-login",
            json={"code": "xiaoyu-code", "miniprogram": "xiaoyu"},
        )

        assert laoyu.status_code == 200
        assert xiaoyu.status_code == 200
        assert xiaoyu.json()["user"]["id"] != laoyu.json()["user"]["id"]

    async def test_wechat_login_rejects_conflicting_union_owners(
        self,
        anonymous_client,
        test_db,
        monkeypatch,
    ):
        first = User(
            email="first-union@example.com",
            password_hash="hash",
            display_name="第一位用户",
        )
        second = User(
            email="second-union@example.com",
            password_hash="hash",
            display_name="第二位用户",
        )
        test_db.add_all([first, second])
        await test_db.flush()
        test_db.add_all(
            [
                AuthIdentity(
                    user_id=first.id,
                    provider="wechat_miniprogram",
                    provider_user_id="laoyu-openid",
                    provider_union_id="conflicting-union",
                ),
                AuthIdentity(
                    user_id=second.id,
                    provider="wechat_miniprogram_xiaoyu",
                    provider_user_id="xiaoyu-openid",
                    provider_union_id="conflicting-union",
                ),
            ]
        )
        await test_db.commit()

        async def fake_exchange(_code: str, _miniprogram: str) -> WeChatSession:
            return WeChatSession(
                openid="laoyu-openid",
                unionid="conflicting-union",
            )

        monkeypatch.setattr("app.routers.auth.exchange_wechat_login_code", fake_exchange)

        response = await anonymous_client.post(
            "/api/auth/wechat-login",
            json={"code": "code"},
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "微信身份关联冲突"

    async def test_wechat_login_rejects_unknown_miniprogram(self, anonymous_client):
        response = await anonymous_client.post(
            "/api/auth/wechat-login",
            json={"code": "code", "miniprogram": "unknown"},
        )

        assert response.status_code == 422

    async def test_set_credentials_enables_web_login_for_wechat_user(
        self,
        anonymous_client,
        monkeypatch,
    ):
        async def fake_exchange(code: str, _miniprogram: str) -> WeChatSession:
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
        async def fake_exchange(code: str, _miniprogram: str) -> WeChatSession:
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
        async def fake_exchange(code: str, _miniprogram: str) -> WeChatSession:
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
        async def fake_exchange(code: str, _miniprogram: str) -> WeChatSession:
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
        async def fake_exchange(code: str, _miniprogram: str) -> WeChatSession:
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
        async def fake_exchange(code: str, _miniprogram: str) -> WeChatSession:
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
        async def fake_exchange(code: str, _miniprogram: str) -> WeChatSession:
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
