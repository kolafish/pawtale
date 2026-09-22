import pytest
from sqlalchemy import func, select

from app.domain.memoir.types import (
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirShareVideoJobStatus,
)
from app.models.memoir import (
    AudioFile,
    MemoirChatMessage,
    MemoirMaterialShareVideo,
    MemoirShareVideoOutput,
    MemoirVoiceUtterance,
)
from app.services.memoir import audio_files as memoir_audio_files
from tests.memoir_helpers import create_test_voice_transcript


async def test_mobile_memoir_routes_require_login(anonymous_client):
    response = await anonymous_client.get("/api/mobile/memoir/me")

    assert response.status_code == 401


@pytest.mark.ci_smoke
async def test_mobile_memoir_project_list_starts_empty(client):
    first = await client.get("/api/mobile/memoir/projects")
    second = await client.get("/api/mobile/memoir/projects")

    assert first.status_code == 200
    assert second.status_code == 200
    first_data = first.json()
    second_data = second.json()
    assert first_data == second_data
    assert first_data["active_project_id"] == ""
    assert first_data["active_project"] is None
    assert first_data["projects"] == []


async def test_mobile_memoir_project_requires_non_empty_title(client):
    missing = await client.post("/api/mobile/memoir/projects", json={})
    blank = await client.post("/api/mobile/memoir/projects", json={"title": "   "})

    assert missing.status_code == 422
    assert blank.status_code == 400
    assert blank.json()["detail"] == "回忆录名称不能为空"


async def test_mobile_memoir_voice_session_requires_explicit_project(client):
    response = await client.post(
        "/api/mobile/memoir/voice-sessions",
        json={"guidance_mode": "free_narration"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "请先创建回忆录"


async def test_mobile_memoir_identity_includes_active_project(client):
    created_response = await client.post("/api/mobile/memoir/projects", json={"title": "母亲的回忆"})
    assert created_response.status_code == 201

    response = await client.get("/api/mobile/memoir/me")

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "tester@example.com"
    assert data["active_project_id"] == data["active_project"]["id"]
    assert data["active_project"]["type"] == "memoir"
    assert len(data["projects"]) == 1
    assert data["projects"][0]["id"] == data["active_project_id"]


async def test_mobile_memoir_snapshots_advance_after_new_material(client, test_db):
    created_response = await client.post(
        "/api/mobile/memoir/projects",
        json={"title": "母亲的回忆"},
    )
    project_id = created_response.json()["id"]

    before = (await client.get("/api/mobile/memoir/home-snapshot")).json()
    created = await create_test_voice_transcript(
        client,
        test_db,
        "小时候我常跟母亲去赶集。",
        project_id=project_id,
    )
    completed_video = MemoirMaterialShareVideo(
        id="completed-snapshot-video",
        project_id=project_id,
        utterance_id=created["utterance_id"],
        audio_file_id=created["audio_file_id"],
        user_id=created["user_id"],
        status=MemoirShareVideoJobStatus.SUCCEEDED.value,
        text_source="edited",
        text_hash="completed-snapshot-video-hash",
        selected_text="小时候我常跟母亲去赶集。",
        start_char=0,
        end_char=13,
        start_ms=0,
        end_ms=8000,
    )
    pending_video = MemoirMaterialShareVideo(
        id="pending-snapshot-video",
        project_id=project_id,
        utterance_id=created["utterance_id"],
        audio_file_id=created["audio_file_id"],
        user_id=created["user_id"],
        status=MemoirShareVideoJobStatus.PENDING.value,
        text_source="edited",
        text_hash="pending-snapshot-video-hash",
        selected_text="小时候我常跟母亲去赶集。",
        start_char=0,
        end_char=13,
        start_ms=0,
        end_ms=8000,
    )
    test_db.add_all([completed_video, pending_video])
    await test_db.flush()
    test_db.add(
        MemoirShareVideoOutput(
            job_id=completed_video.id,
            project_id=project_id,
            storage_path="memoir/share-videos/test/completed-snapshot-video.mp4",
            mime_type="video/mp4",
            size_bytes=1024,
        )
    )
    await test_db.commit()
    after = (await client.get("/api/mobile/memoir/home-snapshot")).json()
    book = (await client.get("/api/mobile/memoir/book-snapshot")).json()

    assert before["identity"]["active_project_id"] == project_id
    assert before["latest_transcript"] is None
    assert after["server_revision"] > before["server_revision"]
    assert after["latest_transcript"]["id"] == created["utterance_id"]
    assert after["latest_transcript"]["display_text"] == "小时候我常跟母亲去赶集。"
    assert book["server_revision"] == after["server_revision"]
    assert book["identity"]["active_project_id"] == project_id
    assert book["plan"] is None
    assert book["drafts_by_chapter_id"] == {}
    assert book["output"] is None
    assert book["automation_events"]["items"] == []
    assert book["image_assets"]["items"] == []
    assert book["share_video_counts"] == [
        {
            "utterance_id": created["utterance_id"],
            "count": 1,
            "job_ids": [completed_video.id],
        }
    ]


async def test_mobile_memoir_projects_can_be_created_updated_and_activated(client):
    created_response = await client.post(
        "/api/mobile/memoir/projects",
        json={
            "title": "母亲的回忆",
            "description": "先从童年聊起",
            "narrator_gender": "female",
            "narrator_origin": "四川成都",
            "narrator_age": 72,
            "default_guidance_mode": "guided_interview",
            "default_interview_type": "ai_interview",
            "default_session_goal": "open_narration",
        },
    )
    assert created_response.status_code == 201
    created = created_response.json()
    assert created["title"] == "母亲的回忆"
    assert created["type"] == "memoir"
    assert created["groups"] == []
    assert created["metadata_json"]["transcript_polish_level"] == "low"
    assert created["metadata_json"]["memoir_profile"] == {
        "narrator_name": "",
        "narrator_gender": "female",
        "narrator_origin": "四川成都",
        "narrator_age": 72,
        "default_guidance_mode": "guided_interview",
        "default_interview_type": "ai_interview",
        "default_session_goal": "open_narration",
    }

    renamed_response = await client.patch(
        f"/api/mobile/memoir/projects/{created['id']}",
        json={
            "title": "外婆的回忆",
            "narrator_gender": "female",
            "narrator_origin": "四川绵阳",
            "narrator_age": 73,
            "default_guidance_mode": "free_narration",
            "default_interview_type": "human_interview",
            "default_session_goal": "open_narration",
            "transcript_polish_level": "high",
        },
    )
    assert renamed_response.status_code == 200
    renamed = renamed_response.json()
    assert renamed["title"] == "外婆的回忆"
    assert renamed["metadata_json"]["memoir_profile"]["narrator_origin"] == "四川绵阳"
    assert renamed["metadata_json"]["memoir_profile"]["narrator_age"] == 73
    assert renamed["metadata_json"]["memoir_profile"]["default_guidance_mode"] == "free_narration"
    assert renamed["metadata_json"]["memoir_profile"]["default_interview_type"] == "human_interview"
    assert renamed["metadata_json"]["memoir_profile"]["default_session_goal"] == "open_narration"
    assert renamed["metadata_json"]["transcript_polish_level"] == "high"

    completion_response = await client.patch(
        f"/api/mobile/memoir/projects/{created['id']}",
        json={
            "default_guidance_mode": "guided_interview",
            "default_interview_type": "ai_interview",
            "default_session_goal": "book_completion",
        },
    )
    assert completion_response.status_code == 200
    completion_profile = completion_response.json()["metadata_json"]["memoir_profile"]
    assert completion_profile["default_guidance_mode"] == "guided_interview"
    assert completion_profile["default_interview_type"] == "ai_interview"
    assert completion_profile["default_session_goal"] == "book_completion"

    project_list_response = await client.get("/api/mobile/memoir/projects")
    project_list = project_list_response.json()
    assert project_list["active_project_id"] == created["id"]
    assert [project["title"] for project in project_list["projects"]] == [
        "外婆的回忆",
    ]

    second_response = await client.post(
        "/api/mobile/memoir/projects",
        json={"title": "父亲的回忆"},
    )
    assert second_response.status_code == 201
    activated_response = await client.post(
        f"/api/mobile/memoir/projects/{created['id']}/activate",
    )
    assert activated_response.status_code == 200
    activated = activated_response.json()
    assert activated["active_project_id"] == created["id"]
    assert activated["active_project"]["title"] == "外婆的回忆"
    assert [project["title"] for project in activated["projects"]] == [
        "外婆的回忆",
        "父亲的回忆",
    ]


async def test_mobile_memoir_project_word_count_includes_user_voice_messages(client, test_db):
    first_project_response = await client.post(
        "/api/mobile/memoir/projects",
        json={"title": "父亲的回忆"},
    )
    first_project_id = first_project_response.json()["id"]
    second_project_response = await client.post(
        "/api/mobile/memoir/projects",
        json={"title": "母亲的回忆"},
    )
    second_project_id = second_project_response.json()["id"]

    first_session_response = await client.post(
        "/api/mobile/memoir/voice-sessions",
        params={"project_id": first_project_id},
        json={"guidance_mode": "free_narration"},
    )
    second_session_response = await client.post(
        "/api/mobile/memoir/voice-sessions",
        params={"project_id": second_project_id},
        json={"guidance_mode": "guided_interview"},
    )
    first_session = first_session_response.json()["session"]
    second_session = second_session_response.json()["session"]
    first_text = " 我小时候 住在外婆家。 "
    second_text = "今天下雨了。"
    test_db.add_all(
        [
            MemoirChatMessage(
                project_id=first_project_id,
                voice_session_id=first_session["id"],
                role=MemoirChatMessageRole.USER.value,
                content_text=first_text,
                content_status=MemoirChatContentStatus.CORRECTED.value,
                turn_index=0,
            ),
            MemoirChatMessage(
                project_id=second_project_id,
                voice_session_id=second_session["id"],
                role=MemoirChatMessageRole.USER.value,
                content_text=second_text,
                content_status=MemoirChatContentStatus.CORRECTED.value,
                turn_index=0,
            ),
            MemoirChatMessage(
                project_id=second_project_id,
                voice_session_id=second_session["id"],
                role=MemoirChatMessageRole.ASSISTANT.value,
                content_text="你想继续聊哪一段？",
                content_status=MemoirChatContentStatus.FINAL.value,
                turn_index=1,
            ),
        ]
    )
    await test_db.commit()

    project_list_response = await client.get("/api/mobile/memoir/projects")
    project_list = project_list_response.json()
    projects = {project["id"]: project for project in project_list["projects"]}

    assert projects[first_project_id]["total_word_count"] == len("".join(first_text.split()))
    assert projects[second_project_id]["total_word_count"] == len(second_text)
    assert project_list["active_project"]["id"] == second_project_id
    assert project_list["active_project"]["total_word_count"] == len(second_text)

    identity_response = await client.get("/api/mobile/memoir/me")
    identity = identity_response.json()
    identity_projects = {project["id"]: project for project in identity["projects"]}

    assert identity["active_project"]["total_word_count"] == len(second_text)
    assert identity_projects[first_project_id]["total_word_count"] == len(
        "".join(first_text.split())
    )


async def test_mobile_memoir_projects_can_be_deleted_but_keeps_one_project(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    user = (await client.get("/api/auth/me")).json()
    default_response = await client.post(
        "/api/mobile/memoir/projects",
        json={"title": "我的回忆录"},
    )
    default_project_id = default_response.json()["id"]

    created_response = await client.post(
        "/api/mobile/memoir/projects",
        json={"title": "父亲的回忆"},
    )
    created_project_id = created_response.json()["id"]
    voice_session_response = await client.post(
        "/api/mobile/memoir/voice-sessions",
        params={"project_id": created_project_id},
        json={"guidance_mode": "free_narration"},
    )
    voice_session_id = voice_session_response.json()["session"]["id"]
    user_audio = (
        await memoir_audio_files.stage_audio_bytes(
            test_db,
            user["id"],
            b"user-audio",
            extension=".pcm",
        )
    ).audio_file
    assistant_audio = (
        await memoir_audio_files.stage_audio_bytes(
            test_db,
            user["id"],
            b"assistant-audio",
            extension=".mp3",
            mime_type="audio/mpeg",
        )
    ).audio_file
    unrelated_audio = (
        await memoir_audio_files.stage_audio_bytes(
            test_db,
            user["id"],
            b"unrelated-audio",
            extension=".pcm",
        )
    ).audio_file
    user_audio_path = memoir_audio_files.audio_file_path(user_audio)
    assistant_audio_path = memoir_audio_files.audio_file_path(assistant_audio)
    unrelated_audio_path = memoir_audio_files.audio_file_path(unrelated_audio)
    test_db.add_all(
        [
            MemoirChatMessage(
                project_id=created_project_id,
                voice_session_id=voice_session_id,
                role=MemoirChatMessageRole.ASSISTANT.value,
                content_text="想继续讲哪一段？",
                content_status=MemoirChatContentStatus.FINAL.value,
                audio_file_id=assistant_audio.id,
                turn_index=0,
            ),
            MemoirVoiceUtterance(
                voice_session_id=voice_session_id,
                audio_file_id=user_audio.id,
                transcript_raw="小时候住在外婆家。",
                transcript_edited="小时候住在外婆家。",
                start_ms=0,
                end_ms=2000,
                provider="test",
                provider_payload_json={},
            ),
        ]
    )
    await test_db.commit()

    assert user_audio_path.exists()
    assert assistant_audio_path.exists()
    assert unrelated_audio_path.exists()

    delete_response = await client.delete(f"/api/mobile/memoir/projects/{created_project_id}")
    assert delete_response.status_code == 204

    project_list_response = await client.get("/api/mobile/memoir/projects")
    project_list = project_list_response.json()
    assert project_list["active_project_id"] == default_project_id
    assert [project["id"] for project in project_list["projects"]] == [default_project_id]
    deleted_audio_count = await test_db.scalar(
        select(func.count(AudioFile.id)).where(
            AudioFile.id.in_([user_audio.id, assistant_audio.id])
        )
    )
    unrelated_audio_count = await test_db.scalar(
        select(func.count(AudioFile.id)).where(AudioFile.id == unrelated_audio.id)
    )
    deleted_message_count = await test_db.scalar(
        select(func.count(MemoirChatMessage.id)).where(
            MemoirChatMessage.project_id == created_project_id
        )
    )
    deleted_utterance_count = await test_db.scalar(
        select(func.count(MemoirVoiceUtterance.id)).where(
            MemoirVoiceUtterance.voice_session_id == voice_session_id
        )
    )
    assert deleted_audio_count == 0
    assert unrelated_audio_count == 1
    assert deleted_message_count == 0
    assert deleted_utterance_count == 0
    assert not user_audio_path.exists()
    assert not assistant_audio_path.exists()
    assert unrelated_audio_path.exists()

    last_delete_response = await client.delete(f"/api/mobile/memoir/projects/{default_project_id}")
    assert last_delete_response.status_code == 400
    assert last_delete_response.json()["detail"] == "至少保留一本回忆录"


async def test_completed_progress_notifications_are_not_hidden_by_old_failures(client, test_db):
    from app.models.memoir import MemoirAutomationEvent
    from tests.memoir_helpers import ensure_mobile_memoir_project

    project_id = await ensure_mobile_memoir_project(client)
    test_db.add_all([
        MemoirAutomationEvent(project_id=project_id, event_type="chapter_draft_generated",
            status="failed", summary_text="旧任务失败")
        for _ in range(8)
    ])
    completed = MemoirAutomationEvent(project_id=project_id, event_type="chapter_draft_generated",
        status="completed", summary_text="新章节正文已完成", metadata_json={"draft_version_id": "new-version"})
    test_db.add(completed)
    await test_db.commit()
    response = await client.get(f"/api/mobile/memoir/projects/{project_id}/automation-events?limit=8&completed_only=true")
    assert response.status_code == 200
    assert [event["id"] for event in response.json()["items"]] == [completed.id]
    assert response.json()["total"] == 1
    snapshot = (await client.get("/api/mobile/memoir/story-snapshot")).json()
    assert [event["id"] for event in snapshot["automation_events"]["items"]] == [completed.id]
    assert not completed.metadata_json.get("acknowledged_at")
