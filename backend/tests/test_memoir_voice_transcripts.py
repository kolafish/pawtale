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
from app.services.memoir.voice_transcripts import _remove_obvious_oral_fillers, generate_cleaned_transcript_text

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


async def _create_voice_transcript(
    client,
    test_db,
    text="嗯，我小时候就是住在外婆家。",
    question_text: str | None = None,
):
    user = (await client.get("/api/auth/me")).json()
    create_response = await client.post(
        "/api/mobile/memoir/voice-sessions",
        json={"guidance_mode": "free_narration", "provider": "volc_streaming_asr"},
    )
    session = create_response.json()["session"]
    audio_file = AudioFile(
        user_id=user["id"],
        storage_path=f"memoir/audio/{user['id']}/sample.pcm",
        mime_type="audio/pcm",
        size_bytes=3200,
        duration_seconds=8,
    )
    test_db.add(audio_file)
    question_audio_file = None
    question_message = None
    if question_text:
        question_audio_file = AudioFile(
            user_id=user["id"],
            storage_path=f"memoir/audio/{user['id']}/question.pcm",
            mime_type="audio/pcm",
            size_bytes=1600,
            duration_seconds=4,
        )
        test_db.add(question_audio_file)
        await test_db.flush()
        question_message = MemoirChatMessage(
            project_id=session["project_id"],
            voice_session_id=session["id"],
            role=MemoirChatMessageRole.ASSISTANT.value,
            content_text=question_text,
            content_status=MemoirChatContentStatus.FINAL.value,
            audio_file_id=question_audio_file.id,
            turn_index=0,
            guidance_intent="opening",
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
        turn_index=1 if question_message else 0,
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
        "user_id": user["id"],
        "session": session,
        "utterance_id": utterance.id,
        "message_id": message.id,
        "audio_file_id": audio_file.id,
        "question_message_id": question_message.id if question_message else None,
        "question_audio_file_id": question_audio_file.id if question_audio_file else None,
    }


async def test_mobile_memoir_lists_saved_voice_transcripts(client, test_db):
    created = await _create_voice_transcript(client, test_db, question_text="你小时候住在哪里？")

    response = await client.get("/api/mobile/memoir/voice-transcripts")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["limit"] == 50
    item = data["items"][0]
    assert item["id"] == created["utterance_id"]
    assert item["voice_session_id"] == created["session"]["id"]
    assert item["project_title"] == "我的回忆录"
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


async def test_mobile_memoir_audio_playback_wraps_pcm_as_wav(client, test_db, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    created = await _create_voice_transcript(client, test_db)
    pcm = b"\x01\x00\x02\x00"
    audio_path = tmp_path / "audio" / created["user_id"] / "sample.pcm"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(pcm)

    response = await client.get(f"/api/mobile/memoir/audio-files/{created['audio_file_id']}/playback")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    assert response.content.startswith(b"RIFF")
    assert response.content[8:12] == b"WAVE"
    assert response.content[-len(pcm):] == pcm


async def test_mobile_memoir_updates_voice_transcript_without_overwriting_raw(client, test_db):
    created = await _create_voice_transcript(client, test_db)

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
    created = await _create_voice_transcript(client, test_db)

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


def _book_source_ref(created: dict, quote: str) -> dict:
    return {
        "voice_session_id": created["session"]["id"],
        "message_id": created["message_id"],
        "utterance_id": created["utterance_id"],
        "transcript_version_id": None,
        "audio_file_id": created["audio_file_id"],
        "start_ms": 0,
        "end_ms": 8000,
        "quote": quote,
    }


async def test_mobile_memoir_lists_book_inclusion_for_voice_transcripts(client, test_db):
    created = await _create_voice_transcript(client, test_db, text="我小时候常在外婆家的院子里晒太阳。")
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
            summary="整理外婆家院子的童年生活。",
            sort_order=0,
            source_refs_json=[_book_source_ref(created, "外婆家的院子")],
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


async def test_mobile_memoir_delete_transcript_removes_book_source_refs_and_marks_draft_stale(client, test_db):
    first = await _create_voice_transcript(client, test_db, text="我小时候常在外婆家的院子里晒太阳。")
    second = await _create_voice_transcript(client, test_db, text="后来我第一次离家去县城读书。")
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
        summary="整理外婆家和第一次离家的内容。",
        sort_order=0,
        source_refs_json=[
            _book_source_ref(first, "外婆家的院子"),
            _book_source_ref(second, "第一次离家"),
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
    created = await _create_voice_transcript(client, test_db)
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


async def test_mobile_memoir_ai_cleanup_removes_repeated_fillers_when_model_is_too_conservative(
    client,
    test_db,
    monkeypatch,
):
    created = await _create_voice_transcript(
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


async def test_generate_cleaned_transcript_text_uses_fallback_when_not_strict():
    result = await generate_cleaned_transcript_text(
        "那个就是今天下雨了，那个那个那个雨下的很大。",
        ai_client=FailingAIClient(),
        strict=False,
    )

    assert result.content == "今天下雨了，雨下的很大。"
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
    created = await _create_voice_transcript(anonymous_client, test_db)
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
