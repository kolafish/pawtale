from sqlalchemy import select

from app.auth import SESSION_COOKIE_NAME
from app.models.memoir import MemoirVoiceAuthorizationConsent
from app.models.memoir_common import AudioFile
from app.models.projects import Project
from app.models.users import AuthIdentity, AuthSession, User
from app.services.wechat_auth import WeChatSession
from tests.memoir_helpers import post_mobile_memoir_voice_session
from tests.project_helpers import create_project


async def test_account_deletion_requires_matching_fresh_wechat_identity(
    anonymous_client, test_db, monkeypatch,
):
    async def exchange(code: str, _miniprogram: str) -> WeChatSession:
        return WeChatSession(openid=f"openid-{code}")

    monkeypatch.setattr("app.routers.auth.exchange_wechat_login_code", exchange)
    login = await anonymous_client.post("/api/auth/wechat-login", json={"code": "owner"})
    user_id = login.json()["user"]["id"]

    denied = await anonymous_client.request("DELETE", "/api/auth/me", json={"code": "another-user"})
    assert denied.status_code == 403
    assert (await anonymous_client.get("/api/auth/me")).status_code == 200
    assert (await test_db.scalars(select(User.id).where(User.id == user_id))).one() == user_id

    missing = await anonymous_client.request("DELETE", "/api/auth/me", json={"code": " "})
    assert missing.status_code == 400
    assert (await anonymous_client.get("/api/auth/me")).status_code == 200


async def test_account_deletion_removes_projects_sessions_consents_and_personal_files(
    anonymous_client, test_db, monkeypatch, tmp_path,
):
    async def exchange(code: str, _miniprogram: str) -> WeChatSession:
        return WeChatSession(openid="owner-openid" if code == "owner" else "wrong-openid")

    monkeypatch.setattr("app.routers.auth.exchange_wechat_login_code", exchange)
    monkeypatch.setattr("app.services.account_deletion.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.services.user_avatars.DATA_DIR", tmp_path)
    login = await anonymous_client.post("/api/auth/wechat-login", json={"code": "owner"})
    user_id = login.json()["user"]["id"]
    old_token = anonymous_client.cookies[SESSION_COOKIE_NAME]
    assert (await anonymous_client.post("/api/auth/wechat-login", json={"code": "owner"})).status_code == 200
    await create_project(anonymous_client, "小说", "novel")
    voice = await post_mobile_memoir_voice_session(
        anonymous_client, {"guidance_mode": "free_narration"},
    )
    assert voice.status_code == 201
    assert (await anonymous_client.post(
        "/api/mobile/memoir/voice-authorization-consent",
        json={"version": "2026-09-21", "agreed": True},
    )).status_code == 201

    audio_dir = tmp_path / "memoir" / "audio" / user_id
    audio_dir.mkdir(parents=True)
    (audio_dir / "orphan.pcm").write_bytes(b"audio")
    test_db.add(AudioFile(
        user_id=user_id, storage_path=f"memoir/audio/{user_id}/orphan.pcm",
        mime_type="audio/pcm", size_bytes=5,
    ))
    avatar_dir = tmp_path / "user-avatars" / user_id
    avatar_dir.mkdir(parents=True)
    (avatar_dir / "old.jpg").write_bytes(b"avatar")
    await test_db.commit()

    deleted = await anonymous_client.request("DELETE", "/api/auth/me", json={"code": "owner"})
    assert deleted.status_code == 204
    assert (await anonymous_client.get("/api/auth/me")).status_code == 401
    anonymous_client.cookies.set(SESSION_COOKIE_NAME, old_token)
    assert (await anonymous_client.get("/api/auth/me")).status_code == 401
    for model in (User, AuthIdentity, AuthSession, Project, MemoirVoiceAuthorizationConsent, AudioFile):
        assert (await test_db.scalars(select(model))).all() == []
    assert not audio_dir.exists()
    assert not avatar_dir.exists()

    anonymous_client.cookies.clear()
    new_login = await anonymous_client.post("/api/auth/wechat-login", json={"code": "owner"})
    assert new_login.status_code == 200
    assert new_login.json()["user"]["id"] != user_id
