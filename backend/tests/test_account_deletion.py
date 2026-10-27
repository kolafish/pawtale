from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.auth import SESSION_COOKIE_NAME
from app.domain.projects.types import ProjectType
from app.models.memoir import MemoirVoiceAuthorizationConsent
from app.models.memoir_common import AudioFile
from app.models.projects import Project
from app.models.users import AuthIdentity, AuthSession, User
from app.services.wechat_auth import WeChatSession
from app.services import account_deletion, content_storage
from app.services.memoir import project_deletion as memoir_project_deletion
from app.services.projects import core as project_core
from tests.memoir_helpers import post_mobile_memoir_voice_session
from tests.project_helpers import create_project


class _AccountDeletionSession:
    def __init__(self, project_ids: list[str], project_files: list, *, fail_commit: bool = False):
        self.project_ids = project_ids
        self.project_files = project_files
        self.fail_commit = fail_commit
        self.pending_deletes = []
        self.commit_count = 0
        self.rollback_count = 0
        self.files_at_commit = []

    async def scalar(self, _statement):
        return None

    async def scalars(self, _statement):
        return SimpleNamespace(all=lambda: self.project_ids)

    async def get(self, _model, _id):
        return None

    async def execute(self, _statement):
        return SimpleNamespace(all=lambda: [])

    async def delete(self, item):
        self.pending_deletes.append(item)

    async def flush(self):
        return None

    async def commit(self):
        self.commit_count += 1
        self.files_at_commit = [path.exists() for path in self.project_files]
        if self.fail_commit:
            raise RuntimeError("final commit failed")

    async def rollback(self):
        self.rollback_count += 1
        self.pending_deletes.clear()


@pytest.mark.parametrize("failure", ["second_project", "final_commit"])
async def test_account_deletion_rolls_back_all_projects_before_file_cleanup(
    monkeypatch, tmp_path, failure,
):
    user = SimpleNamespace(id=str(uuid4()))
    project_ids = ["project-a", "project-b"]
    project_dir = tmp_path / "projects"
    project_files = []
    for project_id in project_ids:
        file = project_dir / project_id / "chapter.md"
        file.parent.mkdir(parents=True)
        file.write_text(project_id)
        project_files.append(file)
    monkeypatch.setattr(content_storage, "PROJECTS_DIR", project_dir)
    monkeypatch.setattr(account_deletion, "DATA_DIR", tmp_path)
    db = _AccountDeletionSession(
        project_ids, project_files, fail_commit=failure == "final_commit",
    )

    async def load_project(_db, project_id, _user_id, *, allow_admin):
        if failure == "second_project" and project_id == project_ids[1]:
            raise RuntimeError("second project failed")
        return SimpleNamespace(id=project_id, user_id=user.id, type=ProjectType.NOVEL.value)

    monkeypatch.setattr(project_core, "load_project", load_project)
    monkeypatch.setattr(project_core, "is_admin_user", lambda _user: False)
    monkeypatch.setattr(project_core, "_stage_project_delete_audit", AsyncMock())

    with pytest.raises(RuntimeError, match="second project failed|final commit failed"):
        await account_deletion.delete_account(db, user)

    assert db.commit_count == (1 if failure == "final_commit" else 0)
    assert db.rollback_count >= 1
    assert db.pending_deletes == []
    assert all(path.read_text() == project_id for path, project_id in zip(project_files, project_ids))
    if failure == "final_commit":
        assert db.files_at_commit == [True, True]


async def test_account_deletion_removes_project_files_only_after_single_commit(
    monkeypatch, tmp_path,
):
    user = SimpleNamespace(id=str(uuid4()))
    project_ids = ["project-a", "project-b"]
    project_dir = tmp_path / "projects"
    project_files = []
    for project_id in project_ids:
        file = project_dir / project_id / "chapter.md"
        file.parent.mkdir(parents=True)
        file.write_text(project_id)
        project_files.append(file)
    monkeypatch.setattr(content_storage, "PROJECTS_DIR", project_dir)
    monkeypatch.setattr(account_deletion, "DATA_DIR", tmp_path)
    monkeypatch.setattr(account_deletion.user_avatars, "avatar_directory", lambda _user_id: tmp_path / "avatars")
    db = _AccountDeletionSession(project_ids, project_files)

    async def load_project(_db, project_id, _user_id, *, allow_admin):
        return SimpleNamespace(id=project_id, user_id=user.id, type=ProjectType.NOVEL.value)

    monkeypatch.setattr(project_core, "load_project", load_project)
    monkeypatch.setattr(project_core, "is_admin_user", lambda _user: False)
    monkeypatch.setattr(project_core, "_stage_project_delete_audit", AsyncMock())

    await account_deletion.delete_account(db, user)

    assert db.commit_count == 1
    assert db.rollback_count == 0
    assert db.files_at_commit == [True, True]
    assert all(not path.exists() for path in project_files)


async def test_memoir_project_deletion_defers_audio_files_until_commit(monkeypatch, tmp_path):
    project_id = "memoir-project"
    project_file = tmp_path / "projects" / project_id / "chapter.md"
    project_file.parent.mkdir(parents=True)
    project_file.write_text("memoir")
    monkeypatch.setattr(content_storage, "PROJECTS_DIR", tmp_path / "projects")
    db = _AccountDeletionSession([], [project_file])
    db.scalar = AsyncMock(return_value=project_id)
    project = SimpleNamespace(id=project_id, metadata_json={})
    audio_file = SimpleNamespace(id="audio-1")
    delete_audio = AsyncMock()
    monkeypatch.setattr(memoir_project_deletion, "_project_audio_files", AsyncMock(return_value=[audio_file]))
    monkeypatch.setattr(memoir_project_deletion, "_purge_project_tables", AsyncMock())
    monkeypatch.setattr(memoir_project_deletion, "_purge_voice_data", AsyncMock())
    monkeypatch.setattr(memoir_project_deletion, "_referenced_audio_ids", AsyncMock(return_value=set()))
    monkeypatch.setattr(memoir_project_deletion, "_remaining_media_paths", AsyncMock(return_value=set()))
    monkeypatch.setattr(memoir_project_deletion.audio_files, "delete_audio_file_paths", delete_audio)
    cleanups = []

    await memoir_project_deletion.delete_project(db, project, post_commit_cleanups=cleanups)

    assert db.commit_count == 0
    assert project_file.exists()
    delete_audio.assert_not_awaited()
    await db.commit()
    for cleanup in cleanups:
        await cleanup()
    assert not project_file.exists()
    delete_audio.assert_awaited_once_with([audio_file])


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
    share_video_dir = tmp_path / "memoir" / "share-videos" / user_id
    share_video_dir.mkdir(parents=True)
    (share_video_dir / "orphan.mp4").write_bytes(b"video")
    background_dir = tmp_path / "memoir" / "share-video-backgrounds" / "uploads" / user_id
    background_dir.mkdir(parents=True)
    (background_dir / "orphan.jpg").write_bytes(b"background")
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
    assert not share_video_dir.exists()
    assert not background_dir.exists()

    anonymous_client.cookies.clear()
    new_login = await anonymous_client.post("/api/auth/wechat-login", json={"code": "owner"})
    assert new_login.status_code == 200
    assert new_login.json()["user"]["id"] != user_id
    credit = (await anonymous_client.get("/api/billing/ai-credit-summary")).json()
    assert float(credit["free_credit_rmb"]) == 5
    assert float(credit["total_credit_rmb"]) == 5

    anonymous_client.cookies.clear()
    other_login = await anonymous_client.post("/api/auth/wechat-login", json={"code": "someone-else"})
    assert other_login.status_code == 200
    other_credit = (await anonymous_client.get("/api/billing/ai-credit-summary")).json()
    assert float(other_credit["free_credit_rmb"]) == 5
