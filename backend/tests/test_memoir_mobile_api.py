from sqlalchemy import func, select

from app.domain.memoir.types import MemoirChatContentStatus, MemoirChatMessageRole
from app.models.memoir import AudioFile, MemoirChatMessage, MemoirVoiceUtterance
from app.services.memoir import audio_files as memoir_audio_files


async def test_mobile_memoir_routes_require_login(anonymous_client):
    response = await anonymous_client.get("/api/mobile/memoir/me")

    assert response.status_code == 401


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
        },
    )
    assert created_response.status_code == 201
    created = created_response.json()
    assert created["title"] == "母亲的回忆"
    assert created["type"] == "memoir"
    assert created["metadata_json"]["memoir_profile"] == {
        "narrator_name": "",
        "narrator_gender": "female",
        "narrator_origin": "四川成都",
        "narrator_age": 72,
        "default_guidance_mode": "guided_interview",
    }

    renamed_response = await client.patch(
        f"/api/mobile/memoir/projects/{created['id']}",
        json={
            "title": "外婆的回忆",
            "narrator_gender": "female",
            "narrator_origin": "四川绵阳",
            "narrator_age": 73,
            "default_guidance_mode": "free_narration",
        },
    )
    assert renamed_response.status_code == 200
    renamed = renamed_response.json()
    assert renamed["title"] == "外婆的回忆"
    assert renamed["metadata_json"]["memoir_profile"]["narrator_origin"] == "四川绵阳"
    assert renamed["metadata_json"]["memoir_profile"]["narrator_age"] == 73
    assert renamed["metadata_json"]["memoir_profile"]["default_guidance_mode"] == "free_narration"

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
    second = second_response.json()
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
    user_audio = await memoir_audio_files.save_audio_bytes(
        test_db,
        user["id"],
        b"user-audio",
        extension=".pcm",
    )
    assistant_audio = await memoir_audio_files.save_audio_bytes(
        test_db,
        user["id"],
        b"assistant-audio",
        extension=".mp3",
        mime_type="audio/mpeg",
    )
    unrelated_audio = await memoir_audio_files.save_audio_bytes(
        test_db,
        user["id"],
        b"unrelated-audio",
        extension=".pcm",
    )
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


async def test_mobile_memoir_legacy_routes_are_removed(client):
    legacy_requests = [
        ("GET", "/api/mobile/memoir/project", None),
        ("GET", "/api/mobile/memoir/dashboard", None),
        ("POST", "/api/mobile/memoir/sessions", {"title": "童年"}),
        ("GET", "/api/mobile/memoir/sessions/current", None),
        ("POST", "/api/mobile/memoir/sessions/legacy-session/process", {}),
        ("GET", "/api/mobile/memoir/sessions/legacy-session/summary", None),
        ("GET", "/api/mobile/memoir/questions/next", None),
        ("POST", "/api/mobile/memoir/answers", {"question_id": "legacy-question", "text": "旧回答"}),
        ("PATCH", "/api/mobile/memoir/answers/legacy-answer", {"text": "旧编辑"}),
        ("POST", "/api/mobile/memoir/answers/legacy-answer/draft", {}),
        ("PATCH", "/api/mobile/memoir/drafts/legacy-draft", {"content_edited": "旧草稿"}),
        ("POST", "/api/mobile/memoir/drafts/legacy-draft/accept", None),
        ("POST", "/api/mobile/memoir/audio-files", {}),
        ("POST", "/api/mobile/memoir/questions/legacy-question/skip", None),
        ("GET", "/api/mobile/memoir/memories/people", None),
        ("GET", "/api/mobile/memoir/memories/events", None),
        ("GET", "/api/mobile/memoir/memories/places", None),
        ("GET", "/api/mobile/memoir/memories/themes", None),
        ("PATCH", "/api/mobile/memoir/memories/person/legacy-memory", {"status": "confirmed"}),
        ("GET", "/api/mobile/memoir/open-questions", None),
        ("PATCH", "/api/mobile/memoir/open-questions/legacy-question", {"status": "dismissed"}),
        ("GET", "/api/mobile/memoir/coverage", None),
        ("POST", "/api/mobile/memoir/coverage/recalculate", None),
        ("GET", "/api/mobile/memoir/question-candidates", None),
        ("POST", "/api/mobile/memoir/question-candidates/generate", None),
        ("POST", "/api/mobile/memoir/question-candidates/legacy-candidate/select", None),
        ("GET", "/api/mobile/memoir/outputs", None),
        ("GET", "/api/mobile/memoir/outputs/legacy-output", None),
        ("GET", "/api/mobile/memoir/summary", None),
        ("GET", "/api/mobile/memoir/stories", None),
        ("GET", "/api/mobile/memoir/stories/legacy-answer", None),
    ]

    for method, path, body in legacy_requests:
        request = getattr(client, method.lower())
        response = await request(path, json=body) if body is not None else await request(path)
        assert response.status_code == 404, f"{method} {path}"
