import json

from sqlalchemy import select

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirCreatedBy,
    MemoirTranscriptVersionType,
)
from app.models.memoir import (
    AudioFile,
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirChatMessage,
    MemoirTranscriptVersion,
    MemoirVoiceUtterance,
)


class FakeBookPlanAI:
    provider_name = "fake-ai"
    model_name = "fake-book-plan"

    def __init__(self):
        self.calls = []

    async def _call_llm(self, system_prompt, user_prompt, **kwargs):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "kwargs": kwargs,
            }
        )
        return json.dumps(
            {
                "title": "外婆回忆录章节规划",
                "chapters": [
                    {
                        "title": "外婆家的童年",
                        "summary": "整理外婆家院子和童年生活。",
                        "source_indices": [1],
                        "evidence_quote": "外婆家的院子",
                    },
                    {
                        "title": "离家求学",
                        "summary": "整理第一次离家去县城读书的经历。",
                        "source_indices": [2],
                        "evidence_quote": "第一次离家去县城读书",
                    },
                ],
            },
            ensure_ascii=False,
        )


async def _active_project_id(client):
    response = await client.get("/api/mobile/memoir/projects")
    assert response.status_code == 200
    return response.json()["active_project_id"]


async def _create_voice_transcript(client, test_db, text: str):
    user = (await client.get("/api/auth/me")).json()
    create_response = await client.post(
        "/api/mobile/memoir/voice-sessions",
        json={"guidance_mode": "free_narration", "provider": "volc_streaming_asr"},
    )
    assert create_response.status_code == 201
    session = create_response.json()["session"]
    audio_file = AudioFile(
        user_id=user["id"],
        storage_path=f"memoir/audio/{user['id']}/{len(text)}.pcm",
        mime_type="audio/pcm",
        size_bytes=3200,
        duration_seconds=8,
    )
    test_db.add(audio_file)
    version = MemoirTranscriptVersion(
        voice_session_id=session["id"],
        version_type=MemoirTranscriptVersionType.ASR_RAW.value,
        content=text,
        provider="volc_streaming_asr",
        created_by=MemoirCreatedBy.ASR.value,
    )
    test_db.add(version)
    await test_db.flush()
    message = MemoirChatMessage(
        project_id=session["project_id"],
        voice_session_id=session["id"],
        role=MemoirChatMessageRole.USER.value,
        content_text=text,
        content_status=MemoirChatContentStatus.FINAL.value,
        audio_file_id=audio_file.id,
        transcript_version_id=version.id,
        turn_index=0,
        metadata_json={"source": "test"},
    )
    test_db.add(message)
    await test_db.flush()
    utterance = MemoirVoiceUtterance(
        voice_session_id=session["id"],
        chat_message_id=message.id,
        audio_file_id=audio_file.id,
        transcript_raw=text,
        transcript_edited=text,
        start_ms=0,
        end_ms=8000,
        provider="volc_streaming_asr",
        provider_payload_json={"source": "test"},
    )
    test_db.add(utterance)
    await test_db.commit()
    return {
        "session": session,
        "message_id": message.id,
        "utterance_id": utterance.id,
        "audio_file_id": audio_file.id,
        "transcript_version_id": version.id,
    }


async def test_mobile_memoir_book_plan_routes_require_login(anonymous_client):
    response = await anonymous_client.post(
        "/api/mobile/memoir/projects/project-1/book-plan/generate",
        json={},
    )

    assert response.status_code == 401


async def test_mobile_memoir_book_source_coverage_requires_login(anonymous_client):
    response = await anonymous_client.get(
        "/api/mobile/memoir/projects/project-1/book-source-coverage",
    )

    assert response.status_code == 401


async def test_mobile_memoir_book_plan_requires_transcripts(client):
    project_id = await _active_project_id(client)

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/generate",
        json={},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "暂无转写内容，无法规划章节"


async def test_mobile_memoir_book_plan_get_returns_404_before_plan_exists(client):
    project_id = await _active_project_id(client)

    response = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-plan")

    assert response.status_code == 404
    assert response.json()["detail"] == "章节规划不存在"


async def test_mobile_memoir_book_source_coverage_lists_all_sources_before_plan(client, test_db):
    created = await _create_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    project_id = created["session"]["project_id"]

    response = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-source-coverage")

    assert response.status_code == 200
    data = response.json()
    assert data["project_id"] == project_id
    assert data["plan_id"] is None
    assert data["total_source_count"] == 1
    assert data["covered_source_count"] == 0
    assert data["uncovered_source_count"] == 1
    assert data["uncovered_sources"][0]["utterance_id"] == created["utterance_id"]
    assert data["uncovered_sources"][0]["text"] == "我小时候常在外婆家的院子里晒太阳。"


async def test_mobile_memoir_book_plan_generate_saves_source_bound_chapters(
    client,
    test_db,
    monkeypatch,
):
    first = await _create_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    second = await _create_voice_transcript(client, test_db, "后来我第一次离家去县城读书，心里很紧张。")
    project_id = first["session"]["project_id"]
    fake_ai = FakeBookPlanAI()
    monkeypatch.setattr(
        "app.services.memoir.book_plans.get_ai_service",
        lambda model=None: fake_ai,
    )

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/generate",
        json={"ai_model": "deepseek", "max_chapters": 6},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "draft"
    assert data["created_by"] == "ai"
    assert data["model"] == "fake-book-plan"
    assert data["metadata_json"]["source_utterance_count"] == 2
    assert [chapter["title"] for chapter in data["chapters"]] == [
        "外婆家的童年",
        "离家求学",
    ]
    assert data["chapters"][0]["source_refs"][0]["utterance_id"] == first["utterance_id"]
    assert data["chapters"][0]["source_refs"][0]["message_id"] == first["message_id"]
    assert data["chapters"][1]["source_refs"][0]["utterance_id"] == second["utterance_id"]
    assert "不新增人物、时间、地点、事件或情绪" in fake_ai.calls[0]["user_prompt"]
    assert f"utterance_id: {first['utterance_id']}" in fake_ai.calls[0]["user_prompt"]

    stored_plan = await test_db.scalar(select(MemoirBookPlan).where(MemoirBookPlan.project_id == project_id))
    stored_chapters = (
        await test_db.execute(
            select(MemoirBookPlanChapter)
            .where(MemoirBookPlanChapter.plan_id == stored_plan.id)
            .order_by(MemoirBookPlanChapter.sort_order)
        )
    ).scalars().all()
    assert stored_plan.status == "draft"
    assert len(stored_chapters) == 2
    assert stored_chapters[0].source_refs_json[0]["quote"] == "外婆家的院子"


async def test_mobile_memoir_book_plan_confirm_replaces_plan_with_user_chapters(
    client,
    test_db,
    monkeypatch,
):
    created = await _create_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    project_id = created["session"]["project_id"]
    monkeypatch.setattr(
        "app.services.memoir.book_plans.get_ai_service",
        lambda model=None: FakeBookPlanAI(),
    )
    generated_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/generate",
        json={},
    )
    source_ref = generated_response.json()["chapters"][0]["source_refs"][0]

    response = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "title": "用户确认后的章节",
            "chapters": [
                {
                    "title": "童年和外婆家",
                    "summary": "只保留童年院子的内容。",
                    "source_refs": [source_ref],
                }
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "用户确认后的章节"
    assert data["status"] == "confirmed"
    assert data["created_by"] == "user"
    assert data["model"] is None
    assert len(data["chapters"]) == 1
    assert data["chapters"][0]["title"] == "童年和外婆家"
    assert data["chapters"][0]["source_refs"][0]["utterance_id"] == created["utterance_id"]

    stored_chapters = (
        await test_db.execute(
            select(MemoirBookPlanChapter).where(MemoirBookPlanChapter.project_id == project_id)
        )
    ).scalars().all()
    assert len(stored_chapters) == 1


async def test_mobile_memoir_book_source_coverage_detects_new_transcripts_after_plan(
    client,
    test_db,
    monkeypatch,
):
    created = await _create_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    project_id = created["session"]["project_id"]
    monkeypatch.setattr(
        "app.services.memoir.book_plans.get_ai_service",
        lambda model=None: FakeBookPlanAI(),
    )
    generated_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/generate",
        json={},
    )
    source_ref = generated_response.json()["chapters"][0]["source_refs"][0]
    confirm_response = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "title": "用户确认后的章节",
            "chapters": [
                {
                    "title": "童年和外婆家",
                    "summary": "只保留童年院子的内容。",
                    "source_refs": [source_ref],
                }
            ],
        },
    )
    assert confirm_response.status_code == 200
    later = await _create_voice_transcript(client, test_db, "后来我又讲了第一次离家去县城读书。")

    response = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-source-coverage")

    assert response.status_code == 200
    data = response.json()
    assert data["plan_id"] == confirm_response.json()["id"]
    assert data["total_source_count"] == 2
    assert data["covered_source_count"] == 1
    assert data["uncovered_source_count"] == 1
    assert [item["utterance_id"] for item in data["uncovered_sources"]] == [later["utterance_id"]]
    assert data["uncovered_sources"][0]["text"] == "后来我又讲了第一次离家去县城读书。"


async def test_mobile_memoir_book_plan_reconfirm_removes_replaced_chapter_drafts(
    client,
    test_db,
    monkeypatch,
):
    created = await _create_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    project_id = created["session"]["project_id"]
    monkeypatch.setattr(
        "app.services.memoir.book_plans.get_ai_service",
        lambda model=None: FakeBookPlanAI(),
    )
    generated_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/generate",
        json={},
    )
    source_ref = generated_response.json()["chapters"][0]["source_refs"][0]
    first_confirm = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "title": "第一版章节",
            "chapters": [
                {
                    "title": "童年和外婆家",
                    "summary": "只保留童年院子的内容。",
                    "source_refs": [source_ref],
                }
            ],
        },
    )
    assert first_confirm.status_code == 200
    replaced_chapter_id = first_confirm.json()["chapters"][0]["id"]
    draft = MemoirBookChapterDraft(
        project_id=project_id,
        chapter_id=replaced_chapter_id,
        status=MemoirBookChapterDraftStatus.CONFIRMED.value,
    )
    test_db.add(draft)
    await test_db.flush()
    test_db.add(
        MemoirBookChapterDraftVersion(
            draft_id=draft.id,
            project_id=project_id,
            chapter_id=replaced_chapter_id,
            version=1,
            content="旧章节正文",
            created_by=MemoirCreatedBy.USER.value,
            prompt_snapshot="",
        )
    )
    await test_db.commit()

    second_confirm = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "title": "第二版章节",
            "chapters": [
                {
                    "title": "重新整理童年",
                    "summary": "重新确认后的章节。",
                    "source_refs": [source_ref],
                }
            ],
        },
    )

    assert second_confirm.status_code == 200
    assert second_confirm.json()["chapters"][0]["id"] != replaced_chapter_id
    assert await test_db.scalar(
        select(MemoirBookChapterDraft).where(MemoirBookChapterDraft.chapter_id == replaced_chapter_id)
    ) is None
    assert await test_db.scalar(
        select(MemoirBookChapterDraftVersion).where(
            MemoirBookChapterDraftVersion.chapter_id == replaced_chapter_id
        )
    ) is None


async def test_mobile_memoir_book_plan_confirm_rejects_foreign_source(client, test_db):
    project_id = await _active_project_id(client)
    await _create_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")

    response = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "chapters": [
                {
                    "title": "伪造来源",
                    "summary": "",
                    "source_refs": [
                        {
                            "voice_session_id": "voice-x",
                            "message_id": "message-x",
                            "utterance_id": "utterance-x",
                            "quote": "不存在的来源",
                        }
                    ],
                }
            ]
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "章节来源不属于当前回忆录"
