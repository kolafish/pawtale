from sqlalchemy import select

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
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
from tests.memoir_helpers import post_mobile_memoir_voice_session


class FakeBookDraftAI:
    provider_name = "fake-ai"
    model_name = "fake-book-draft"

    def __init__(self, content: str = "我小时候常在外婆家的院子里晒太阳。"):
        self.content = content
        self.calls = []

    async def _call_llm(self, system_prompt, user_prompt, **kwargs):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "kwargs": kwargs,
            }
        )
        return self.content


async def _active_project_id(client):
    response = await client.get("/api/mobile/memoir/projects")
    assert response.status_code == 200
    active_project_id = response.json()["active_project_id"]
    if active_project_id:
        return active_project_id
    created = await client.post("/api/mobile/memoir/projects", json={"title": "测试回忆录"})
    assert created.status_code == 201
    return created.json()["id"]


async def _create_voice_transcript(client, test_db, text: str):
    user = (await client.get("/api/auth/me")).json()
    create_response = await post_mobile_memoir_voice_session(
        client,
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


async def _create_book_plan(test_db, *, project_id: str, source_ref: dict, status: MemoirBookPlanStatus):
    plan = MemoirBookPlan(
        project_id=project_id,
        title="外婆回忆录",
        status=status.value,
        created_by=MemoirCreatedBy.USER.value,
    )
    test_db.add(plan)
    await test_db.flush()
    chapter = MemoirBookPlanChapter(
        plan_id=plan.id,
        project_id=project_id,
        title="外婆家的童年",
        summary="整理外婆家院子和童年生活。",
        sort_order=0,
        source_refs_json=[source_ref],
    )
    test_db.add(chapter)
    await test_db.commit()
    return plan, chapter


def _source_ref(created: dict, quote: str = "外婆家的院子") -> dict:
    return {
        "voice_session_id": created["session"]["id"],
        "message_id": created["message_id"],
        "utterance_id": created["utterance_id"],
        "transcript_version_id": created["transcript_version_id"],
        "audio_file_id": created["audio_file_id"],
        "start_ms": 0,
        "end_ms": 8000,
        "quote": quote,
    }


async def test_mobile_memoir_book_chapter_draft_routes_require_login(anonymous_client):
    response = await anonymous_client.post(
        "/api/mobile/memoir/projects/project-1/book-plan/chapters/chapter-1/draft",
        json={},
    )

    assert response.status_code == 401


async def test_mobile_memoir_book_chapter_draft_requires_confirmed_plan(client, test_db):
    created = await _create_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    project_id = created["session"]["project_id"]
    _, chapter = await _create_book_plan(
        test_db,
        project_id=project_id,
        source_ref=_source_ref(created),
        status=MemoirBookPlanStatus.DRAFT,
    )

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft",
        json={},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "请先确认章节规划"


async def test_mobile_memoir_book_chapter_draft_get_returns_404_before_draft_exists(client, test_db):
    created = await _create_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    project_id = created["session"]["project_id"]
    _, chapter = await _create_book_plan(
        test_db,
        project_id=project_id,
        source_ref=_source_ref(created),
        status=MemoirBookPlanStatus.CONFIRMED,
    )

    response = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft"
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "章节稿不存在"


async def test_mobile_memoir_book_chapter_draft_generate_uses_chapter_sources(
    client,
    test_db,
    monkeypatch,
):
    first = await _create_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    second = await _create_voice_transcript(client, test_db, "后来我第一次离家去县城读书，心里很紧张。")
    project_id = first["session"]["project_id"]
    _, chapter = await _create_book_plan(
        test_db,
        project_id=project_id,
        source_ref=_source_ref(first),
        status=MemoirBookPlanStatus.CONFIRMED,
    )
    fake_ai = FakeBookDraftAI("我小时候常在外婆家的院子里晒太阳。")
    monkeypatch.setattr(
        "app.services.memoir.book_drafts.get_ai_service",
        lambda model=None: fake_ai,
    )

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft",
        json={"ai_model": "deepseek"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "draft"
    assert data["latest_version"]["version"] == 1
    assert data["latest_version"]["content"] == "我小时候常在外婆家的院子里晒太阳。"
    assert data["latest_version"]["created_by"] == "ai"
    assert data["latest_version"]["model"] == "fake-book-draft"
    assert data["versions"][0]["prompt_snapshot"]
    prompt = fake_ai.calls[0]["user_prompt"]
    assert "我小时候常在外婆家的院子里晒太阳" in prompt
    assert "第一次离家去县城读书" not in prompt
    assert f"utterance_id: {first['utterance_id']}" in prompt
    assert f"utterance_id: {second['utterance_id']}" not in prompt
    assert "不新增事实、人物、时间、地点、事件、对话或情绪" in prompt

    stored_draft = await test_db.scalar(
        select(MemoirBookChapterDraft).where(MemoirBookChapterDraft.chapter_id == chapter.id)
    )
    stored_versions = (
        await test_db.execute(
            select(MemoirBookChapterDraftVersion)
            .where(MemoirBookChapterDraftVersion.draft_id == stored_draft.id)
            .order_by(MemoirBookChapterDraftVersion.version)
        )
    ).scalars().all()
    assert len(stored_versions) == 1
    assert stored_versions[0].created_by == "ai"


async def test_mobile_memoir_book_chapter_draft_regenerate_appends_version_with_feedback(
    client,
    test_db,
    monkeypatch,
):
    created = await _create_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    project_id = created["session"]["project_id"]
    _, chapter = await _create_book_plan(
        test_db,
        project_id=project_id,
        source_ref=_source_ref(created),
        status=MemoirBookPlanStatus.CONFIRMED,
    )
    fake_ai = FakeBookDraftAI("第一版正文")
    monkeypatch.setattr(
        "app.services.memoir.book_drafts.get_ai_service",
        lambda model=None: fake_ai,
    )
    first_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft",
        json={},
    )
    fake_ai.content = "第二版正文"

    second_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft",
        json={"force_regenerate": True, "feedback": "更贴近原话"},
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    data = second_response.json()
    assert data["latest_version"]["version"] == 2
    assert data["latest_version"]["content"] == "第二版正文"
    assert data["latest_version"]["feedback"] == "更贴近原话"
    assert len(data["versions"]) == 2


async def test_mobile_memoir_book_chapter_draft_supplements_pending_sources_without_force(
    client,
    test_db,
    monkeypatch,
):
    first = await _create_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    second = await _create_voice_transcript(client, test_db, "后来我又讲了第一次离家去县城读书。")
    project_id = first["session"]["project_id"]
    _, chapter = await _create_book_plan(
        test_db,
        project_id=project_id,
        source_ref=_source_ref(first),
        status=MemoirBookPlanStatus.CONFIRMED,
    )
    chapter.source_refs_json = [_source_ref(first), _source_ref(second, "第一次离家")]
    chapter.metadata_json = {
        "pending_source_utterance_ids": [second["utterance_id"]],
        "source_assignment_updated_at": "2026-06-17T00:00:00+00:00",
    }
    draft = MemoirBookChapterDraft(
        project_id=project_id,
        chapter_id=chapter.id,
        status=MemoirBookChapterDraftStatus.DRAFT.value,
        metadata_json={"status_reason": "new_sources_assigned"},
    )
    test_db.add(draft)
    await test_db.flush()
    test_db.add(
        MemoirBookChapterDraftVersion(
            draft_id=draft.id,
            project_id=project_id,
            chapter_id=chapter.id,
            version=1,
            content="我小时候常在外婆家的院子里晒太阳。",
            created_by=MemoirCreatedBy.AI.value,
            prompt_snapshot="old prompt",
        )
    )
    await test_db.commit()
    fake_ai = FakeBookDraftAI("我小时候常在外婆家的院子里晒太阳。后来我第一次离家去县城读书。")
    monkeypatch.setattr(
        "app.services.memoir.book_drafts.get_ai_service",
        lambda model=None: fake_ai,
    )

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft",
        json={},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["latest_version"]["version"] == 2
    assert "第一次离家去县城读书" in data["latest_version"]["content"]
    assert data["metadata_json"] == {}
    assert len(fake_ai.calls) == 1
    prompt = fake_ai.calls[0]["user_prompt"]
    assert "原章节正文" in prompt
    assert "本次任务：只把 source_type 为“新增来源”" in prompt
    assert f"utterance_id: {first['utterance_id']}" in prompt
    assert f"utterance_id: {second['utterance_id']}" in prompt
    assert "source_type: 既有来源" in prompt
    assert "source_type: 新增来源" in prompt

    refreshed_chapter = await test_db.scalar(
        select(MemoirBookPlanChapter).where(MemoirBookPlanChapter.id == chapter.id)
    )
    assert refreshed_chapter.metadata_json == {}


async def test_mobile_memoir_book_chapter_draft_update_clears_pending_source_state(client, test_db):
    first = await _create_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    second = await _create_voice_transcript(client, test_db, "后来我又讲了第一次离家去县城读书。")
    project_id = first["session"]["project_id"]
    _, chapter = await _create_book_plan(
        test_db,
        project_id=project_id,
        source_ref=_source_ref(first),
        status=MemoirBookPlanStatus.CONFIRMED,
    )
    chapter.source_refs_json = [_source_ref(first), _source_ref(second, "第一次离家")]
    chapter.metadata_json = {
        "pending_source_utterance_ids": [second["utterance_id"]],
        "source_assignment_updated_at": "2026-06-17T00:00:00+00:00",
    }
    draft = MemoirBookChapterDraft(
        project_id=project_id,
        chapter_id=chapter.id,
        status=MemoirBookChapterDraftStatus.DRAFT.value,
        metadata_json={"status_reason": "new_sources_assigned"},
    )
    test_db.add(draft)
    await test_db.flush()
    test_db.add(
        MemoirBookChapterDraftVersion(
            draft_id=draft.id,
            project_id=project_id,
            chapter_id=chapter.id,
            version=1,
            content="我小时候常在外婆家的院子里晒太阳。",
            created_by=MemoirCreatedBy.AI.value,
            prompt_snapshot="old prompt",
        )
    )
    await test_db.commit()

    response = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft",
        json={"content": "我小时候常在外婆家的院子里晒太阳。后来我第一次离家去县城读书。"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["latest_version"]["version"] == 2
    assert data["latest_version"]["created_by"] == "user"
    assert data["metadata_json"] == {}
    refreshed_chapter = await test_db.scalar(
        select(MemoirBookPlanChapter).where(MemoirBookPlanChapter.id == chapter.id)
    )
    assert refreshed_chapter.metadata_json == {}


async def test_mobile_memoir_book_chapter_draft_confirm_rejects_pending_sources(client, test_db):
    created = await _create_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    project_id = created["session"]["project_id"]
    _, chapter = await _create_book_plan(
        test_db,
        project_id=project_id,
        source_ref=_source_ref(created),
        status=MemoirBookPlanStatus.CONFIRMED,
    )
    chapter.metadata_json = {"pending_source_utterance_ids": [created["utterance_id"]]}
    draft = MemoirBookChapterDraft(
        project_id=project_id,
        chapter_id=chapter.id,
        status=MemoirBookChapterDraftStatus.DRAFT.value,
    )
    test_db.add(draft)
    await test_db.flush()
    test_db.add(
        MemoirBookChapterDraftVersion(
            draft_id=draft.id,
            project_id=project_id,
            chapter_id=chapter.id,
            version=1,
            content="我小时候常在外婆家的院子里晒太阳。",
            created_by=MemoirCreatedBy.AI.value,
            prompt_snapshot="",
        )
    )
    await test_db.commit()

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft/confirm"
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "请先补写新增来源"


async def test_mobile_memoir_book_chapter_draft_confirm_clears_removed_source_state(client, test_db):
    created = await _create_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    project_id = created["session"]["project_id"]
    _, chapter = await _create_book_plan(
        test_db,
        project_id=project_id,
        source_ref=_source_ref(created),
        status=MemoirBookPlanStatus.CONFIRMED,
    )
    chapter.metadata_json = {
        "removed_source_utterance_ids": ["deleted-utterance"],
        "source_refs_removed_at": "2026-06-17T00:00:00+00:00",
    }
    draft = MemoirBookChapterDraft(
        project_id=project_id,
        chapter_id=chapter.id,
        status=MemoirBookChapterDraftStatus.DRAFT.value,
        metadata_json={"status_reason": "sources_deleted", "removed_source_utterance_ids": ["deleted-utterance"]},
    )
    test_db.add(draft)
    await test_db.flush()
    test_db.add(
        MemoirBookChapterDraftVersion(
            draft_id=draft.id,
            project_id=project_id,
            chapter_id=chapter.id,
            version=1,
            content="我小时候常在外婆家的院子里晒太阳。",
            created_by=MemoirCreatedBy.USER.value,
            prompt_snapshot="",
        )
    )
    await test_db.commit()

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft/confirm"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "confirmed"
    assert data["metadata_json"] == {}
    refreshed_chapter = await test_db.scalar(
        select(MemoirBookPlanChapter).where(MemoirBookPlanChapter.id == chapter.id)
    )
    assert refreshed_chapter.metadata_json == {}


async def test_mobile_memoir_book_chapter_draft_update_and_confirm(client, test_db, monkeypatch):
    created = await _create_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    project_id = created["session"]["project_id"]
    _, chapter = await _create_book_plan(
        test_db,
        project_id=project_id,
        source_ref=_source_ref(created),
        status=MemoirBookPlanStatus.CONFIRMED,
    )
    monkeypatch.setattr(
        "app.services.memoir.book_drafts.get_ai_service",
        lambda model=None: FakeBookDraftAI("AI 初稿"),
    )
    await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft",
        json={},
    )

    update_response = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft",
        json={"content": "用户确认前改过的章节正文"},
    )
    confirm_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft/confirm"
    )

    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["status"] == "draft"
    assert updated["latest_version"]["version"] == 2
    assert updated["latest_version"]["content"] == "用户确认前改过的章节正文"
    assert updated["latest_version"]["created_by"] == "user"
    assert confirm_response.status_code == 200
    confirmed = confirm_response.json()
    assert confirmed["status"] == "confirmed"
    assert confirmed["latest_version"]["content"] == "用户确认前改过的章节正文"


async def test_mobile_memoir_book_chapter_draft_post_returns_existing_without_force(
    client,
    test_db,
    monkeypatch,
):
    created = await _create_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    project_id = created["session"]["project_id"]
    _, chapter = await _create_book_plan(
        test_db,
        project_id=project_id,
        source_ref=_source_ref(created),
        status=MemoirBookPlanStatus.CONFIRMED,
    )
    fake_ai = FakeBookDraftAI("第一版正文")
    monkeypatch.setattr(
        "app.services.memoir.book_drafts.get_ai_service",
        lambda model=None: fake_ai,
    )

    first_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft",
        json={},
    )
    second_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft",
        json={},
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert second_response.json()["latest_version"]["version"] == 1
    assert len(fake_ai.calls) == 1
