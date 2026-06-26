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
from app.services.memoir.asr_text_normalization import normalize_memoir_asr_text
from app.services.memoir.voice_transcripts import _remove_obvious_oral_fillers, generate_cleaned_transcript_text
from tests.memoir_helpers import create_test_voice_transcript, post_mobile_memoir_voice_session, memoir_source_ref

UTC_SUFFIXES = ("Z", "+00:00")


class FakeAIClient:
    provider_name = "fake-ai"
    model_name = "fake-cleaner"

    async def _call_llm(self, *args, **kwargs):
        return "我小时候住在外婆家。"


class EchoFillerAIClient:
    provider_name = "fake-ai"
    model_name = "fake-cleaner"

    async def _call_llm(self, *args, **kwargs):
        return "那个就是今天下雨了，那个那个那个雨下的很大。"


class FailingAIClient:
    provider_name = "fake-ai"
    model_name = "fake-cleaner"

    async def _call_llm(self, *args, **kwargs):
        raise RuntimeError("provider down")


async def _append_voice_turn(
    test_db,
    *,
    user_id: str,
    session: dict,
    text: str,
    question_text: str,
    question_turn_index: int,
    user_turn_index: int,
) -> dict:
    audio_file = AudioFile(
        user_id=user_id,
        storage_path=f"memoir/audio/{user_id}/sample-{user_turn_index}.pcm",
        mime_type="audio/pcm",
        size_bytes=3200,
        duration_seconds=8,
    )
    test_db.add(audio_file)
    question_message = MemoirChatMessage(
        project_id=session["project_id"],
        voice_session_id=session["id"],
        role=MemoirChatMessageRole.ASSISTANT.value,
        content_text=question_text,
        content_status=MemoirChatContentStatus.FINAL.value,
        turn_index=question_turn_index,
        guidance_intent="follow_up",
        metadata_json={"source": "test.question"},
    )
    test_db.add(question_message)
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
        turn_index=user_turn_index,
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
        "utterance_id": utterance.id,
        "message_id": message.id,
        "question_message_id": question_message.id,
    }


async def test_mobile_memoir_lists_saved_voice_transcripts(client, test_db):
    created = await create_test_voice_transcript(client, test_db, question_text="你小时候住在哪里？")

    response = await client.get("/api/mobile/memoir/voice-transcripts")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["limit"] == 50
    item = data["items"][0]
    assert item["id"] == created["utterance_id"]
    assert item["voice_session_id"] == created["session"]["id"]
    assert item["project_title"] == "测试回忆录"
    assert item["chat_message_id"] == created["message_id"]
    assert item["audio_file_id"] == created["audio_file_id"]
    assert item["question_message_id"] == created["question_message_id"]
    assert item["question_text"] == "你小时候住在哪里？"
    assert item["question_audio_file_id"] == created["question_audio_file_id"]
    assert item["audio_mime_type"] == "audio/pcm"
    assert item["audio_size_bytes"] == 3200
    assert item["audio_duration_seconds"] == 8
    assert item["display_text"] == "嗯，我小时候就是住在外婆家。"
    assert item["display_char_count"] == len("嗯，我小时候就是住在外婆家。")
    assert item["is_included_in_book"] is False
    assert item["included_chapters"] == []
    assert item["created_at"].endswith(UTC_SUFFIXES)
    assert item["session_created_at"].endswith(UTC_SUFFIXES)


async def test_mobile_memoir_searches_voice_transcripts_by_text_and_question(client, test_db):
    first = await create_test_voice_transcript(
        client,
        test_db,
        text="我小时候常在外婆家的院子里晒太阳。",
        question_text="你小时候住在哪里？",
    )
    second = await create_test_voice_transcript(
        client,
        test_db,
        text="后来我第一次离家去县城读书。",
        question_text="你在学校最想念谁？",
    )

    text_response = await client.get("/api/mobile/memoir/voice-transcripts", params={"q": "外婆"})
    fts_response = await client.get("/api/mobile/memoir/voice-transcripts", params={"q": "外婆家"})
    question_response = await client.get("/api/mobile/memoir/voice-transcripts", params={"q": "学校"})
    multi_term_response = await client.get("/api/mobile/memoir/voice-transcripts", params={"q": "县城 读书"})
    empty_response = await client.get("/api/mobile/memoir/voice-transcripts", params={"q": "纺织厂"})

    assert text_response.status_code == 200
    assert text_response.json()["total"] == 1
    assert text_response.json()["items"][0]["id"] == first["utterance_id"]

    assert fts_response.status_code == 200
    assert fts_response.json()["total"] == 1
    assert fts_response.json()["items"][0]["id"] == first["utterance_id"]

    assert question_response.status_code == 200
    assert question_response.json()["total"] == 1
    assert question_response.json()["items"][0]["id"] == second["utterance_id"]

    assert multi_term_response.status_code == 200
    assert multi_term_response.json()["total"] == 1
    assert multi_term_response.json()["items"][0]["id"] == second["utterance_id"]

    assert empty_response.status_code == 200
    assert empty_response.json()["total"] == 0
    assert empty_response.json()["items"] == []


async def test_mobile_memoir_searches_only_the_displayed_question_for_each_transcript(client, test_db):
    user = (await client.get("/api/auth/me")).json()
    create_response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "guided_interview", "provider": "volc_streaming_asr"},
    )
    session = create_response.json()["session"]
    first = await _append_voice_turn(
        test_db,
        user_id=user["id"],
        session=session,
        question_text="你小时候最喜欢的老师是谁？",
        text="那时候我经常在老家院子里玩。",
        question_turn_index=0,
        user_turn_index=1,
    )
    second = await _append_voice_turn(
        test_db,
        user_id=user["id"],
        session=session,
        question_text="后来去县城读书时住在哪里？",
        text="我住在学校旁边的一间小屋里。",
        question_turn_index=2,
        user_turn_index=3,
    )

    teacher_response = await client.get("/api/mobile/memoir/voice-transcripts", params={"q": "老师"})
    county_response = await client.get("/api/mobile/memoir/voice-transcripts", params={"q": "县城"})

    assert teacher_response.status_code == 200
    assert teacher_response.json()["total"] == 1
    assert teacher_response.json()["items"][0]["id"] == first["utterance_id"]

    assert county_response.status_code == 200
    assert county_response.json()["total"] == 1
    assert county_response.json()["items"][0]["id"] == second["utterance_id"]


async def test_mobile_memoir_filters_voice_transcripts_by_mode_and_module(client, test_db):
    await create_test_voice_transcript(
        client,
        test_db,
        text="我想自由讲讲小时候的村子。",
        guidance_mode="free_narration",
    )
    guided = await create_test_voice_transcript(
        client,
        test_db,
        text="母亲每天很早就去田里干活。",
        guidance_mode="guided_interview",
        current_guidance_module="person",
    )

    response = await client.get(
        "/api/mobile/memoir/voice-transcripts",
        params={"guidance_mode": "guided_interview", "module": "person"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == guided["utterance_id"]
    assert data["items"][0]["guidance_mode"] == "guided_interview"
    assert data["items"][0]["current_guidance_module"] == "person"


async def test_mobile_memoir_audio_playback_wraps_pcm_as_wav(client, test_db, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    created = await create_test_voice_transcript(client, test_db)
    pcm = b"\x01\x00\x02\x00"
    audio_path = tmp_path / "audio" / created["audio_relative_path"]
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(pcm)

    response = await client.get(f"/api/mobile/memoir/audio-files/{created['audio_file_id']}/playback")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    assert response.content.startswith(b"RIFF")
    assert response.content[8:12] == b"WAVE"
    assert response.content[-len(pcm):] == pcm


async def test_mobile_memoir_updates_voice_transcript_without_overwriting_raw(client, test_db):
    created = await create_test_voice_transcript(client, test_db)

    response = await client.patch(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}",
        json={"transcript_edited": "我小时候住在外婆家。"},
    )

    assert response.status_code == 200
    item = response.json()
    assert item["transcript_raw"] == "嗯，我小时候就是住在外婆家。"
    assert item["transcript_edited"] == "我小时候住在外婆家。"
    assert item["display_text"] == "我小时候住在外婆家。"

    utterance = await test_db.scalar(
        select(MemoirVoiceUtterance).where(MemoirVoiceUtterance.id == created["utterance_id"])
    )
    message = await test_db.scalar(select(MemoirChatMessage).where(MemoirChatMessage.id == created["message_id"]))
    versions = (
        await test_db.execute(
            select(MemoirTranscriptVersion)
            .where(MemoirTranscriptVersion.voice_session_id == created["session"]["id"])
            .order_by(MemoirTranscriptVersion.created_at)
        )
    ).scalars().all()

    assert utterance.transcript_raw == "嗯，我小时候就是住在外婆家。"
    assert utterance.transcript_edited == "我小时候住在外婆家。"
    assert message.content_text == "我小时候住在外婆家。"
    assert message.content_status == "corrected"
    assert len(versions) == 2
    assert versions[-1].version_type == "user_edited"
    assert versions[-1].created_by == "user"
    assert message.transcript_version_id == versions[-1].id


async def test_mobile_memoir_deletes_voice_transcript_and_user_message(client, test_db):
    created = await create_test_voice_transcript(client, test_db)

    response = await client.delete(f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}")

    assert response.status_code == 204

    list_response = await client.get("/api/mobile/memoir/voice-transcripts")
    assert list_response.status_code == 200
    assert list_response.json()["total"] == 0

    utterance = await test_db.scalar(
        select(MemoirVoiceUtterance).where(MemoirVoiceUtterance.id == created["utterance_id"])
    )
    message = await test_db.scalar(select(MemoirChatMessage).where(MemoirChatMessage.id == created["message_id"]))
    audio_file = await test_db.scalar(select(AudioFile).where(AudioFile.id == created["audio_file_id"]))
    versions = (
        await test_db.execute(
            select(MemoirTranscriptVersion).where(
                MemoirTranscriptVersion.voice_session_id == created["session"]["id"]
            )
        )
    ).scalars().all()

    assert utterance is None
    assert message is None
    assert audio_file is not None
    assert len(versions) == 1
    assert versions[0].content == "嗯，我小时候就是住在外婆家。"

    progress_response = await client.get("/api/mobile/memoir/voice-progress")
    assert progress_response.status_code == 200
    progress = progress_response.json()
    assert progress["session_count"] == 0
    assert progress["utterance_count"] == 0
    assert progress["user_message_count"] == 0
    assert progress["total_user_char_count"] == 0
    assert progress["total_audio_seconds"] == 0
    assert progress["modules"] == []


async def test_mobile_memoir_lists_book_inclusion_for_voice_transcripts(client, test_db):
    created = await create_test_voice_transcript(client, test_db, text="我小时候常在外婆家的院子里晒太阳。")
    project_id = created["session"]["project_id"]
    plan = MemoirBookPlan(
        project_id=project_id,
        title="外婆回忆录章节规划",
        status=MemoirBookPlanStatus.CONFIRMED.value,
        created_by=MemoirCreatedBy.USER.value,
    )
    test_db.add(plan)
    await test_db.flush()
    test_db.add(
        MemoirBookPlanChapter(
            plan_id=plan.id,
            project_id=project_id,
            title="院子里的童年",
            sort_order=0,
            source_refs_json=[memoir_source_ref(created, "外婆家的院子", transcript_version_id=None)],
        )
    )
    await test_db.commit()

    response = await client.get(f"/api/mobile/memoir/voice-transcripts?project_id={project_id}")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["id"] == created["utterance_id"]
    assert item["is_included_in_book"] is True
    assert len(item["included_chapters"]) == 1
    assert item["included_chapters"][0]["title"] == "院子里的童年"
    assert item["included_chapters"][0]["sort_order"] == 0


async def test_mobile_memoir_filters_voice_transcripts_by_book_inclusion(client, test_db):
    included = await create_test_voice_transcript(client, test_db, text="我小时候常在外婆家的院子里晒太阳。")
    not_included = await create_test_voice_transcript(client, test_db, text="后来我第一次离家去县城读书。")
    project_id = included["session"]["project_id"]
    plan = MemoirBookPlan(
        project_id=project_id,
        title="外婆回忆录章节规划",
        status=MemoirBookPlanStatus.CONFIRMED.value,
        created_by=MemoirCreatedBy.USER.value,
    )
    test_db.add(plan)
    await test_db.flush()
    test_db.add(
        MemoirBookPlanChapter(
            plan_id=plan.id,
            project_id=project_id,
            title="院子里的童年",
            sort_order=0,
            source_refs_json=[memoir_source_ref(included, "外婆家的院子", transcript_version_id=None)],
        )
    )
    await test_db.commit()

    included_response = await client.get(
        "/api/mobile/memoir/voice-transcripts",
        params={"project_id": project_id, "included": "included"},
    )
    not_included_response = await client.get(
        "/api/mobile/memoir/voice-transcripts",
        params={"project_id": project_id, "included": "not_included"},
    )

    assert included_response.status_code == 200
    assert included_response.json()["total"] == 1
    assert included_response.json()["items"][0]["id"] == included["utterance_id"]

    assert not_included_response.status_code == 200
    assert not_included_response.json()["total"] == 1
    assert not_included_response.json()["items"][0]["id"] == not_included["utterance_id"]


async def test_mobile_memoir_delete_transcript_removes_book_source_refs_and_marks_draft_stale(client, test_db):
    first = await create_test_voice_transcript(client, test_db, text="我小时候常在外婆家的院子里晒太阳。")
    second = await create_test_voice_transcript(client, test_db, text="后来我第一次离家去县城读书。")
    project_id = first["session"]["project_id"]
    plan = MemoirBookPlan(
        project_id=project_id,
        title="外婆回忆录章节规划",
        status=MemoirBookPlanStatus.CONFIRMED.value,
        created_by=MemoirCreatedBy.USER.value,
    )
    test_db.add(plan)
    await test_db.flush()
    chapter = MemoirBookPlanChapter(
        plan_id=plan.id,
        project_id=project_id,
        title="童年和离家",
        sort_order=0,
        source_refs_json=[
            memoir_source_ref(first, "外婆家的院子", transcript_version_id=None),
            memoir_source_ref(second, "第一次离家", transcript_version_id=None),
        ],
    )
    test_db.add(chapter)
    await test_db.flush()
    draft = MemoirBookChapterDraft(
        project_id=project_id,
        chapter_id=chapter.id,
        status=MemoirBookChapterDraftStatus.CONFIRMED.value,
    )
    test_db.add(draft)
    await test_db.flush()
    test_db.add(
        MemoirBookChapterDraftVersion(
            draft_id=draft.id,
            project_id=project_id,
            chapter_id=chapter.id,
            version=1,
            content="我小时候常在外婆家的院子里晒太阳。后来我第一次离家去县城读书。",
            created_by=MemoirCreatedBy.USER.value,
            prompt_snapshot="",
        )
    )
    await test_db.commit()
    generated = await client.post(f"/api/mobile/memoir/projects/{project_id}/book-output/generate", json={})
    assert generated.status_code == 200

    response = await client.delete(f"/api/mobile/memoir/voice-transcripts/{second['utterance_id']}")

    assert response.status_code == 204
    refreshed_chapter = await test_db.scalar(
        select(MemoirBookPlanChapter).where(MemoirBookPlanChapter.id == chapter.id)
    )
    assert [ref["utterance_id"] for ref in refreshed_chapter.source_refs_json] == [first["utterance_id"]]
    assert refreshed_chapter.metadata_json["removed_source_utterance_ids"] == [second["utterance_id"]]
    assert "source_refs_removed_at" in refreshed_chapter.metadata_json

    refreshed_draft = await test_db.scalar(
        select(MemoirBookChapterDraft).where(MemoirBookChapterDraft.id == draft.id)
    )
    assert refreshed_draft.status == MemoirBookChapterDraftStatus.DRAFT.value
    assert refreshed_draft.metadata_json["status_reason"] == "sources_deleted"
    assert refreshed_draft.metadata_json["removed_source_utterance_ids"] == [second["utterance_id"]]

    output = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-output")
    assert output.status_code == 200
    assert output.json()["is_stale"] is True
    assert output.json()["stale_chapter_titles"] == ["童年和离家"]


async def test_mobile_memoir_ai_cleanup_creates_corrected_version(client, test_db, monkeypatch):
    created = await create_test_voice_transcript(client, test_db)
    monkeypatch.setattr(
        "app.services.memoir.voice_transcripts.get_ai_service",
        lambda model=None: FakeAIClient(),
    )

    response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/cleanup",
        json={"ai_model": "deepseek"},
    )

    assert response.status_code == 200
    item = response.json()
    assert item["transcript_raw"] == "嗯，我小时候就是住在外婆家。"
    assert item["transcript_edited"] == "我小时候住在外婆家。"

    message = await test_db.scalar(select(MemoirChatMessage).where(MemoirChatMessage.id == created["message_id"]))
    version = await test_db.scalar(
        select(MemoirTranscriptVersion).where(MemoirTranscriptVersion.id == message.transcript_version_id)
    )
    assert version.version_type == "dialect_corrected"
    assert version.created_by == "ai"
    assert version.provider == "fake-ai"
    assert version.content == "我小时候住在外婆家。"


async def test_mobile_memoir_transcript_edits_keep_only_latest_three_versions(client, test_db):
    created = await create_test_voice_transcript(client, test_db)

    for index in range(5):
        response = await client.patch(
            f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}",
            json={"transcript_edited": f"第 {index + 1} 次人工整理"},
        )
        assert response.status_code == 200

    versions = (
        await test_db.execute(
            select(MemoirTranscriptVersion)
            .where(
                MemoirTranscriptVersion.source_artifact_id == created["utterance_id"],
                MemoirTranscriptVersion.version_type == MemoirTranscriptVersionType.USER_EDITED.value,
            )
            .order_by(MemoirTranscriptVersion.created_at, MemoirTranscriptVersion.id)
        )
    ).scalars().all()
    message = await test_db.scalar(select(MemoirChatMessage).where(MemoirChatMessage.id == created["message_id"]))

    assert [version.content for version in versions] == [
        "第 3 次人工整理",
        "第 4 次人工整理",
        "第 5 次人工整理",
    ]
    assert message.transcript_version_id == versions[-1].id


async def test_mobile_memoir_ai_cleanup_removes_repeated_fillers_when_model_is_too_conservative(
    client,
    test_db,
    monkeypatch,
):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="那个就是今天下雨了，那个那个那个雨下的很大。",
    )
    monkeypatch.setattr(
        "app.services.memoir.voice_transcripts.get_ai_service",
        lambda model=None: EchoFillerAIClient(),
    )

    response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/cleanup",
        json={},
    )

    assert response.status_code == 200
    item = response.json()
    assert item["transcript_raw"] == "那个就是今天下雨了，那个那个那个雨下的很大。"
    assert item["transcript_edited"] == "今天下雨了，雨下的很大。"


def test_remove_obvious_oral_fillers_preserves_meaningful_deictic_phrase():
    assert _remove_obvious_oral_fillers("那个年代生活很不一样。") == "那个年代生活很不一样。"
    assert _remove_obvious_oral_fillers("那个那个那个雨下得很大。") == "雨下得很大。"


def test_normalize_memoir_asr_text_corrects_chapter_homophones_in_book_context():
    assert normalize_memoir_asr_text("现在有几个张杰啊？") == "现在有几个章节啊？"
    assert normalize_memoir_asr_text("这条还没收入张杰。") == "这条还没收入章节。"
    assert normalize_memoir_asr_text("你不是一个人名的张杰，是书的张杰。") == "你不是一个人名的章节，是书的章节。"
    assert normalize_memoir_asr_text("把第一张改名为童年往事。") == "把第一章改名为童年往事。"
    assert normalize_memoir_asr_text("张杰这个人，在书里单独占了一章。") == "张杰这个人，在书里单独占了一章。"


async def test_generate_cleaned_transcript_text_uses_fallback_when_not_strict():
    result = await generate_cleaned_transcript_text(
        "那个就是今天下雨了，那个那个那个雨下的很大。",
        ai_client=FailingAIClient(),
        strict=False,
    )

    assert result.content == "今天下雨了，雨下的很大。"
    assert result.provider is None
    assert result.created_by == MemoirCreatedBy.SYSTEM


async def test_generate_cleaned_transcript_text_normalizes_book_domain_asr_terms_when_not_strict():
    result = await generate_cleaned_transcript_text(
        "现在有几个张杰啊？",
        ai_client=FailingAIClient(),
        strict=False,
    )

    assert result.content == "现在有几个章节啊？"
    assert result.provider is None
    assert result.created_by == MemoirCreatedBy.SYSTEM


async def test_mobile_memoir_voice_transcripts_are_user_scoped(anonymous_client, test_db):
    first_register = await anonymous_client.post(
        "/api/auth/register",
        json={
            "email": "voice-transcript-first@example.com",
            "password": "password123",
            "display_name": "第一位作者",
        },
    )
    assert first_register.status_code == 201
    created = await create_test_voice_transcript(anonymous_client, test_db)
    await anonymous_client.post("/api/auth/logout")

    second_register = await anonymous_client.post(
        "/api/auth/register",
        json={
            "email": "voice-transcript-second@example.com",
            "password": "password123",
            "display_name": "第二位作者",
        },
    )
    assert second_register.status_code == 201

    list_response = await anonymous_client.get("/api/mobile/memoir/voice-transcripts")
    update_response = await anonymous_client.patch(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}",
        json={"transcript_edited": "跨用户编辑"},
    )
    delete_response = await anonymous_client.delete(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}"
    )

    assert list_response.status_code == 200
    assert list_response.json()["total"] == 0
    assert update_response.status_code == 404
    assert delete_response.status_code == 404
