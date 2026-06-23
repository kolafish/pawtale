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
from tests.memoir_helpers import post_mobile_memoir_voice_session


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


class FakeChapterTitleAI:
    provider_name = "fake-ai"
    model_name = "fake-title"

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
        return "外婆院中的日子"


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


def _source_ref(created: dict[str, str], quote: str = "") -> dict[str, str]:
    return {
        "voice_session_id": created["session"]["id"],
        "message_id": created["message_id"],
        "utterance_id": created["utterance_id"],
        "transcript_version_id": created["transcript_version_id"],
        "audio_file_id": created["audio_file_id"],
        "quote": quote,
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


async def test_mobile_memoir_book_source_assignments_require_login(anonymous_client):
    response = await anonymous_client.post(
        "/api/mobile/memoir/projects/project-1/book-source-assignments",
        json={"assignments": [{"chapter_id": "chapter-1", "utterance_ids": ["utterance-1"]}]},
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
    assert data["sources"][0]["utterance_id"] == created["utterance_id"]
    assert data["sources"][0]["is_included"] is False
    assert data["sources"][0]["included_in_chapters"] == []
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
    assert data["chapters"][0]["source_refs"][0]["created_at"]
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

    legacy_ref = dict(stored_chapters[0].source_refs_json[0])
    legacy_ref.pop("created_at", None)
    stored_chapters[0].source_refs_json = [legacy_ref]
    test_db.add(stored_chapters[0])
    await test_db.commit()

    get_response = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-plan")
    assert get_response.status_code == 200
    assert get_response.json()["chapters"][0]["source_refs"][0]["created_at"]


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
    source_status_by_utterance = {item["utterance_id"]: item for item in data["sources"]}
    assert source_status_by_utterance[created["utterance_id"]]["is_included"] is True
    assert source_status_by_utterance[created["utterance_id"]]["included_in_chapters"][0]["title"] == "童年和外婆家"
    assert source_status_by_utterance[later["utterance_id"]]["is_included"] is False
    assert [item["utterance_id"] for item in data["uncovered_sources"]] == [later["utterance_id"]]
    assert data["uncovered_sources"][0]["text"] == "后来我又讲了第一次离家去县城读书。"


async def test_mobile_memoir_book_source_assignment_appends_source_and_marks_draft_pending(
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
    chapter_id = confirm_response.json()["chapters"][0]["id"]
    draft = MemoirBookChapterDraft(
        project_id=project_id,
        chapter_id=chapter_id,
        status=MemoirBookChapterDraftStatus.CONFIRMED.value,
    )
    test_db.add(draft)
    await test_db.flush()
    test_db.add(
        MemoirBookChapterDraftVersion(
            draft_id=draft.id,
            project_id=project_id,
            chapter_id=chapter_id,
            version=1,
            content="我小时候常在外婆家的院子里晒太阳。",
            created_by=MemoirCreatedBy.USER.value,
            prompt_snapshot="",
        )
    )
    await test_db.commit()
    later = await _create_voice_transcript(client, test_db, "后来我又讲了第一次离家去县城读书。")

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-source-assignments",
        json={
            "assignments": [
                {
                    "chapter_id": chapter_id,
                    "utterance_ids": [later["utterance_id"]],
                }
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()
    refs = data["chapters"][0]["source_refs"]
    assert [ref["utterance_id"] for ref in refs] == [created["utterance_id"], later["utterance_id"]]
    assert later["utterance_id"] in data["chapters"][0]["metadata_json"]["pending_source_utterance_ids"]

    refreshed_draft = await test_db.scalar(
        select(MemoirBookChapterDraft).where(MemoirBookChapterDraft.id == draft.id)
    )
    assert refreshed_draft.status == MemoirBookChapterDraftStatus.DRAFT.value
    assert refreshed_draft.metadata_json["status_reason"] == "new_sources_assigned"

    coverage = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-source-coverage")
    assert coverage.status_code == 200
    assert coverage.json()["uncovered_source_count"] == 0


async def test_mobile_memoir_book_source_assignment_rejects_covered_source(
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
    chapter_id = confirm_response.json()["chapters"][0]["id"]

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-source-assignments",
        json={
            "assignments": [
                {
                    "chapter_id": chapter_id,
                    "utterance_ids": [created["utterance_id"]],
                }
            ],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "转写已收录到章节"


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


async def test_mobile_memoir_book_plan_patch_deletes_chapter_without_replacing_remaining_draft(
    client,
    test_db,
    monkeypatch,
):
    first = await _create_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    second = await _create_voice_transcript(client, test_db, "后来我第一次离家去县城读书，心里很紧张。")
    project_id = first["session"]["project_id"]
    monkeypatch.setattr(
        "app.services.memoir.book_plans.get_ai_service",
        lambda model=None: FakeBookPlanAI(),
    )
    generated_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/generate",
        json={},
    )
    generated = generated_response.json()
    confirm_response = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "title": "用户确认后的章节",
            "chapters": [
                {
                    "title": "童年和外婆家",
                    "summary": "只保留童年院子的内容。",
                    "source_refs": [generated["chapters"][0]["source_refs"][0]],
                },
                {
                    "title": "离家求学",
                    "summary": "只保留离家读书的内容。",
                    "source_refs": [generated["chapters"][1]["source_refs"][0]],
                },
            ],
        },
    )
    assert confirm_response.status_code == 200
    first_chapter, second_chapter = confirm_response.json()["chapters"]
    first_draft = MemoirBookChapterDraft(
        project_id=project_id,
        chapter_id=first_chapter["id"],
        status=MemoirBookChapterDraftStatus.CONFIRMED.value,
    )
    second_draft = MemoirBookChapterDraft(
        project_id=project_id,
        chapter_id=second_chapter["id"],
        status=MemoirBookChapterDraftStatus.CONFIRMED.value,
    )
    test_db.add_all([first_draft, second_draft])
    await test_db.flush()
    test_db.add_all([
        MemoirBookChapterDraftVersion(
            draft_id=first_draft.id,
            project_id=project_id,
            chapter_id=first_chapter["id"],
            version=1,
            content="童年正文",
            created_by=MemoirCreatedBy.USER.value,
            prompt_snapshot="",
        ),
        MemoirBookChapterDraftVersion(
            draft_id=second_draft.id,
            project_id=project_id,
            chapter_id=second_chapter["id"],
            version=1,
            content="离家正文",
            created_by=MemoirCreatedBy.USER.value,
            prompt_snapshot="",
        ),
    ])
    await test_db.commit()

    response = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "title": "调整后的章节",
            "chapters": [
                {
                    "id": first_chapter["id"],
                    "title": first_chapter["title"],
                    "summary": first_chapter["summary"],
                    "source_refs": first_chapter["source_refs"],
                }
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "调整后的章节"
    assert [chapter["id"] for chapter in data["chapters"]] == [first_chapter["id"]]
    assert await test_db.scalar(
        select(MemoirBookChapterDraft).where(MemoirBookChapterDraft.id == first_draft.id)
    ) is not None
    assert await test_db.scalar(
        select(MemoirBookChapterDraft).where(MemoirBookChapterDraft.id == second_draft.id)
    ) is None

    coverage = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-source-coverage")
    assert coverage.status_code == 200
    assert [item["utterance_id"] for item in coverage.json()["uncovered_sources"]] == [second["utterance_id"]]


async def test_mobile_memoir_book_plan_patch_adds_chapter_from_uncovered_source(
    client,
    test_db,
    monkeypatch,
):
    first = await _create_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    project_id = first["session"]["project_id"]
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
    existing_chapter = confirm_response.json()["chapters"][0]
    later = await _create_voice_transcript(client, test_db, "后来我又讲了一个认识很多年的朋友。")

    response = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "chapters": [
                {
                    "id": existing_chapter["id"],
                    "title": existing_chapter["title"],
                    "summary": existing_chapter["summary"],
                    "source_refs": existing_chapter["source_refs"],
                },
                {
                    "title": "朋友往事",
                    "summary": "整理新收录的朋友相关转写。",
                    "source_refs": [_source_ref(later, "认识很多年的朋友")],
                },
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert [chapter["title"] for chapter in data["chapters"]] == ["童年和外婆家", "朋友往事"]
    assert data["chapters"][0]["id"] == existing_chapter["id"]
    assert data["chapters"][1]["id"] != existing_chapter["id"]
    assert data["chapters"][1]["source_refs"][0]["utterance_id"] == later["utterance_id"]

    coverage = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-source-coverage")
    assert coverage.status_code == 200
    assert coverage.json()["uncovered_source_count"] == 0


async def test_mobile_memoir_book_plan_patch_updates_draft_plan_structure(
    client,
    test_db,
    monkeypatch,
):
    first = await _create_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    second = await _create_voice_transcript(client, test_db, "后来我第一次离家去县城读书，心里很紧张。")
    project_id = first["session"]["project_id"]
    monkeypatch.setattr(
        "app.services.memoir.book_plans.get_ai_service",
        lambda model=None: FakeBookPlanAI(),
    )
    generated_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/generate",
        json={},
    )
    assert generated_response.status_code == 200
    generated = generated_response.json()
    assert generated["status"] == "draft"
    first_chapter, second_chapter = generated["chapters"]

    response = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "title": "草稿目录调整",
            "chapters": [
                {
                    "id": first_chapter["id"],
                    "title": "童年和离家",
                    "summary": "合并整理童年院子和第一次离家。",
                    "source_refs": [
                        first_chapter["source_refs"][0],
                        second_chapter["source_refs"][0],
                    ],
                }
            ],
        },
    )

    assert response.status_code == 200, response.json()
    data = response.json()
    assert data["status"] == "draft"
    assert data["title"] == "草稿目录调整"
    assert [chapter["title"] for chapter in data["chapters"]] == ["童年和离家"]
    assert [ref["utterance_id"] for ref in data["chapters"][0]["source_refs"]] == [
        first["utterance_id"],
        second["utterance_id"],
    ]

    coverage = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-source-coverage")
    assert coverage.status_code == 200
    assert coverage.json()["uncovered_source_count"] == 0


async def test_mobile_memoir_book_chapter_title_update_renames_confirmed_chapter(
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
            "chapters": [
                {
                    "title": "童年和外婆家",
                    "summary": "只保留童年院子的内容。",
                    "source_refs": [source_ref],
                }
            ],
        },
    )
    chapter_id = confirm_response.json()["chapters"][0]["id"]

    response = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter_id}/title",
        json={"title": "院子里的童年"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["chapters"][0]["title"] == "院子里的童年"
    assert data["chapters"][0]["source_refs"][0]["utterance_id"] == created["utterance_id"]
    assert data["chapters"][0]["metadata_json"]["title_updated_by"] == "user"


async def test_mobile_memoir_book_chapter_title_generate_uses_chapter_sources(
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
            "chapters": [
                {
                    "title": "新收录转写",
                    "summary": "整理这条新收录转写。",
                    "source_refs": [source_ref],
                }
            ],
        },
    )
    chapter_id = confirm_response.json()["chapters"][0]["id"]
    fake_ai = FakeChapterTitleAI()
    monkeypatch.setattr(
        "app.services.memoir.book_plans.get_ai_service",
        lambda model=None: fake_ai,
    )

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter_id}/title/generate",
        json={"ai_model": "deepseek"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["chapters"][0]["title"] == "外婆院中的日子"
    assert data["chapters"][0]["metadata_json"]["title_updated_by"] == "ai"
    assert data["chapters"][0]["metadata_json"]["title_model"] == "fake-title"
    assert "不要使用“转写”" in fake_ai.calls[0]["user_prompt"]
    assert "外婆家的院子" in fake_ai.calls[0]["user_prompt"]


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
