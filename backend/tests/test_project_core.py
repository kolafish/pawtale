from datetime import datetime

from sqlalchemy import func, select

from app.models.audit import AuditDailyFeatureUsage, AuditEvent, AuditSession
from app.models.memoir import (
    MemoirImageAsset,
    MemoirMaterialShareVideo,
    MemoirShareVideoOutput,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.memoir_common import AudioFile
from app.models.users import User
from project_helpers import create_project, project_payload


async def _register_admin(client, test_db) -> dict:
    await client.post("/api/auth/logout")
    response = await client.post(
        "/api/auth/register",
        json={
            "email": "admin@example.com",
            "password": "password123",
            "display_name": "管理员",
        },
    )
    assert response.status_code == 201
    admin = await test_db.scalar(select(User).where(User.email == "admin@example.com"))
    assert admin is not None
    admin.role = "admin"
    await test_db.commit()
    return response.json()["user"]


class TestProjectAPI:
    """Project CRUD tests."""

    async def test_list_projects_empty(self, client):
        response = await client.get("/api/projects")
        assert response.status_code == 200
        assert response.json() == []

    async def test_list_projects_filters_by_type(self, client):
        article = await create_project(client, "一篇文章", "article")
        await create_project(client, "一本小说", "novel")

        response = await client.get("/api/projects", params={"type": "article"})

        assert response.status_code == 200
        assert [project["id"] for project in response.json()] == [article["id"]]

    async def test_list_projects_rejects_unknown_type(self, client):
        response = await client.get("/api/projects", params={"type": "unknown"})

        assert response.status_code == 422

    async def test_create_project(self, client):
        response = await client.post("/api/projects", json=project_payload("我的小说"))
        assert response.status_code == 201
        data = response.json()
        assert data["type"] == "novel"
        assert data["title"] == "我的小说"
        assert len(data["groups"]) == 1
        assert data["groups"][0]["name"] == "第一卷"
        assert len(data["groups"][0]["documents"]) == 1

    async def test_create_project_requires_type(self, client):
        response = await client.post("/api/projects", json={"title": "缺少类型"})
        assert response.status_code == 422

    async def test_project_type_templates(self, client):
        cases = {
            "novel": ("第一卷", "未命名文档"),
            "article": ("正文", "未命名文章"),
        }
        for project_type, (group_name, document_name) in cases.items():
            response = await client.post("/api/projects", json=project_payload(f"{project_type} 项目", project_type))
            assert response.status_code == 201
            data = response.json()
            assert data["type"] == project_type
            assert len(data["groups"]) == 1
            assert data["groups"][0]["name"] == group_name
            assert len(data["groups"][0]["documents"]) == 1
            assert data["groups"][0]["documents"][0]["name"] == document_name
            assert data["references"] == []

        memoir = await client.post(
            "/api/projects",
            json=project_payload("memoir 项目", "memoir"),
        )
        assert memoir.status_code == 201
        assert memoir.json()["groups"] == []
        assert memoir.json()["references"] == []

    async def test_get_project(self, client):
        project = await create_project(client, "测试项目")
        response = await client.get(f"/api/projects/{project['id']}")
        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "novel"
        assert data["title"] == "测试项目"

    async def test_admin_can_view_other_users_memoir_project_but_not_update_project(self, client, test_db):
        owner = (await client.get("/api/auth/me")).json()
        project = await create_project(client, "余仲尤的往事", "memoir")

        await _register_admin(client, test_db)

        list_response = await client.get("/api/projects")
        assert list_response.status_code == 200
        listed_project = next(item for item in list_response.json() if item["id"] == project["id"])
        assert listed_project["owner_user_id"] == owner["id"]
        assert listed_project["owner_email"] == owner["email"]
        assert listed_project["owner_display_name"] == owner["display_name"]
        assert listed_project["can_edit"] is False
        assert listed_project["can_delete"] is True
        assert listed_project["can_edit_memoir_materials"] is True

        detail_response = await client.get(f"/api/projects/{project['id']}")
        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert detail["title"] == "余仲尤的往事"
        assert detail["owner_user_id"] == owner["id"]
        assert detail["can_edit"] is False
        assert detail["can_delete"] is True
        assert detail["can_edit_memoir_materials"] is True

        update_response = await client.put(
            f"/api/projects/{project['id']}",
            json={"title": "管理员不应改项目标题"},
        )
        assert update_response.status_code == 404

    async def test_admin_can_delete_other_users_project_with_server_audit(self, client, test_db):
        owner = (await client.get("/api/auth/me")).json()
        project = await create_project(client, "需要管理员清理的项目", "memoir")

        await _register_admin(client, test_db)

        response = await client.delete(f"/api/projects/{project['id']}")

        assert response.status_code == 204
        assert (await client.get(f"/api/projects/{project['id']}")).status_code == 404
        assert await test_db.get(User, owner["id"]) is not None

        event = await test_db.scalar(
            select(AuditEvent).where(AuditEvent.feature_code == "admin.project.delete")
        )
        assert event is not None
        assert event.platform == "web"
        assert event.page_path == "/admin/projects"
        assert event.project_id is None
        assert event.metadata_json == {
            "deleted_project_id": project["id"],
            "deleted_project_type": "memoir",
            "deleted_project_title": "需要管理员清理的项目",
            "owner_user_id": owner["id"],
            "owner_email": owner["email"],
            "owner_display_name": owner["display_name"],
            "actor_role": "admin",
        }
        feature_usage = await test_db.scalar(
            select(AuditDailyFeatureUsage).where(
                AuditDailyFeatureUsage.user_id == event.user_id,
                AuditDailyFeatureUsage.platform == "web",
                AuditDailyFeatureUsage.feature_code == "admin.project.delete",
            )
        )
        assert feature_usage is not None
        assert feature_usage.event_count == 1

    async def test_non_admin_cannot_view_other_users_project(self, client):
        project = await create_project(client, "别人项目", "memoir")
        await client.post("/api/auth/logout")
        register = await client.post(
            "/api/auth/register",
            json={
                "email": "viewer@example.com",
                "password": "password123",
                "display_name": "普通查看者",
            },
        )
        assert register.status_code == 201

        list_response = await client.get("/api/projects")
        assert list_response.status_code == 200
        assert all(item["id"] != project["id"] for item in list_response.json())

        detail_response = await client.get(f"/api/projects/{project['id']}")
        assert detail_response.status_code == 404

        delete_response = await client.delete(f"/api/projects/{project['id']}")
        assert delete_response.status_code == 404

    async def test_update_project(self, client):
        project = await create_project(client, "原标题")
        response = await client.put(
            f"/api/projects/{project['id']}",
            json={"title": "新标题", "word_count_target": 50000},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "新标题"
        assert data["word_count_target"] == 50000

    async def test_delete_project(self, client):
        project = await create_project(client, "将被删除")
        response = await client.delete(f"/api/projects/{project['id']}")
        assert response.status_code == 204
        get_resp = await client.get(f"/api/projects/{project['id']}")
        assert get_resp.status_code == 404

    async def test_generic_delete_purges_memoir_narrative_data_and_media(
        self,
        client,
        test_db,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.setattr("app.services.memoir.project_deletion.DATA_DIR", tmp_path)
        monkeypatch.setattr("app.services.memoir.audio_files.DATA_DIR", tmp_path)
        project = await create_project(client, "完整删除", "memoir")
        user = await test_db.scalar(
            select(User).where(User.email == "tester@example.com")
        )
        assert user is not None
        image_path = tmp_path / "memoir" / project["id"] / "asset.png"
        thumbnail_path = tmp_path / "memoir" / project["id"] / "asset.thumb.png"
        audio_path = tmp_path / "memoir" / project["id"] / "source.wav"
        video_path = tmp_path / "memoir" / project["id"] / "share.mp4"
        image_path.parent.mkdir(parents=True)
        image_path.write_bytes(b"image")
        thumbnail_path.write_bytes(b"thumbnail")
        audio_path.write_bytes(b"audio")
        video_path.write_bytes(b"video")
        test_db.add_all(
            [
                AuditSession(
                    id="delete-project-audit-session",
                    user_id=user.id,
                    client_session_id="delete-project-client-session",
                    platform="web",
                ),
                MemoirImageAsset(
                    project_id=project["id"],
                    user_id=user.id,
                    source_type="cover_candidate",
                    source_signature="delete-project-image",
                    storage_path=str(image_path.relative_to(tmp_path)),
                    thumbnail_storage_path=str(thumbnail_path.relative_to(tmp_path)),
                    mime_type="image/png",
                ),
                AudioFile(
                    id="delete-project-audio",
                    user_id=user.id,
                    storage_path=str(audio_path.relative_to(tmp_path)),
                    mime_type="audio/wav",
                    size_bytes=audio_path.stat().st_size,
                ),
                MemoirVoiceSession(
                    id="delete-project-voice-session",
                    project_id=project["id"],
                ),
                MemoirVoiceUtterance(
                    id="delete-project-utterance",
                    voice_session_id="delete-project-voice-session",
                    audio_file_id="delete-project-audio",
                    transcript_raw="要删除的语音",
                ),
                MemoirMaterialShareVideo(
                    id="delete-project-video-job",
                    project_id=project["id"],
                    utterance_id="delete-project-utterance",
                    audio_file_id="delete-project-audio",
                    user_id=user.id,
                    status="succeeded",
                    text_source="raw",
                    text_hash="delete-project-hash",
                    selected_text="要删除的语音",
                    start_char=0,
                    end_char=6,
                    start_ms=0,
                    end_ms=3000,
                ),
                MemoirShareVideoOutput(
                    id="delete-project-video-output",
                    job_id="delete-project-video-job",
                    project_id=project["id"],
                    storage_path=str(video_path.relative_to(tmp_path)),
                    mime_type="video/mp4",
                    size_bytes=video_path.stat().st_size,
                ),
            ]
        )
        await test_db.commit()
        test_db.add(
            AuditEvent(
                user_id=user.id,
                session_id="delete-project-audit-session",
                client_event_id="delete-project-event",
                event_type="page_view",
                event_date=datetime.now().date(),
                platform="web",
                project_id=project["id"],
                occurred_at=datetime.now(),
            )
        )
        await test_db.commit()

        response = await client.delete(f"/api/projects/{project['id']}")

        assert response.status_code == 204
        assert await test_db.scalar(
            select(func.count(MemoirImageAsset.id)).where(
                MemoirImageAsset.project_id == project["id"]
            )
        ) == 0
        assert await test_db.scalar(
            select(func.count(MemoirShareVideoOutput.id)).where(
                MemoirShareVideoOutput.project_id == project["id"]
            )
        ) == 0
        assert not image_path.exists()
        assert not thumbnail_path.exists()
        assert not audio_path.exists()
        assert not video_path.exists()
        audit_event = await test_db.scalar(
            select(AuditEvent).where(
                AuditEvent.client_event_id == "delete-project-event"
            )
        )
        assert audit_event is not None
        assert audit_event.project_id is None

    async def test_project_not_found(self, client):
        response = await client.get("/api/projects/nonexistent")
        assert response.status_code == 404
