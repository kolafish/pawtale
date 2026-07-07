import json
from pathlib import Path

from sqlalchemy import select

from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirChapterSourceLinkStatus,
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirCreatedBy,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
    MemoirSemanticCacheStatus,
    MemoirTranscriptVersionType,
)
from app.models.memoir import (
    AudioFile,
    MemoirAutomationEvent,
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirChapterSourceLink,
    MemoirChatMessage,
    MemoirEditingJob,
    MemoirSourceCard,
    MemoirTranscriptVersion,
    MemoirVoiceUtterance,
)
from app.services.memoir import source_cards
from app.services.memoir import automation_event_resolution
from app.services.memoir.asr_text_normalization import normalize_memoir_asr_text
from app.services.memoir.voice_transcripts import (
    MemoirTranscriptCleanupContext,
    _remove_obvious_oral_fillers,
    generate_cleaned_transcript_text,
)
from app.services.memoir.utterance_editing_pipeline import (
    ROUTE_PAYLOAD_SOURCE_TEXT_HASHES_KEY,
    ROUTE_PAYLOAD_TRANSCRIPT_VERSION_IDS_KEY,
    source_text_hash,
)
from tests.memoir_helpers import create_test_voice_transcript, post_mobile_memoir_voice_session

UTC_SUFFIXES = ("Z", "+00:00")


def _chapter_source_link(
    *,
    project_id: str,
    plan_id: str,
    chapter_id: str,
    created: dict,
    quote: str,
) -> MemoirChapterSourceLink:
    return MemoirChapterSourceLink(
        project_id=project_id,
        plan_id=plan_id,
        chapter_id=chapter_id,
        utterance_id=created["utterance_id"],
        transcript_version_id=created.get("transcript_version_id"),
        audio_file_id=created.get("audio_file_id"),
        quote=quote,
        status=MemoirChapterSourceLinkStatus.ACTIVE.value,
    )


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


class CapturingAIClient:
    provider_name = "fake-ai"
    model_name = "fake-cleaner"

    def __init__(self, response: str = "现在有几个章节啊？") -> None:
        self.response = response
        self.prompt = ""

    async def _call_llm(self, _system_prompt, user_prompt, **_kwargs):
        self.prompt = user_prompt
        return self.response


async def add_ready_source_card(
    test_db,
    created: dict,
    *,
    short_title: str = "外婆家院子里的童年",
    summary: str = "讲述者回忆小时候住在外婆家，在院子里生活的片段。",
    source_text: str | None = None,
) -> MemoirSourceCard:
    if source_text is None:
        utterance = await test_db.scalar(
            select(MemoirVoiceUtterance).where(MemoirVoiceUtterance.id == created["utterance_id"])
        )
        assert utterance is not None
        source_text = source_cards.source_text_for_utterance(utterance)
    card = MemoirSourceCard(
        project_id=created["session"]["project_id"],
        utterance_id=created["utterance_id"],
        transcript_version_id=created["transcript_version_id"],
        source_text_hash=source_cards.source_text_hash(source_text),
        engine=source_cards.SOURCE_CARD_ENGINE,
        status=MemoirSemanticCacheStatus.READY.value,
        card_json={
            "short_title": short_title,
            "summary": summary,
            "people": [],
            "places": [],
            "time_hints": [],
            "event_tags": [],
            "emotion_tags": [],
            "key_facts": [],
            "open_questions": [],
        },
    )
    test_db.add(card)
    await test_db.commit()
    return card


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
    assert item["summary_title"] == "嗯，我小时候就是住在外婆家。"
    assert item["summary_text"] == "嗯，我小时候就是住在外婆家。"
    assert item["summary_source"] == "fallback"
    assert item["display_char_count"] == len("嗯，我小时候就是住在外婆家。")
    assert item["is_included_in_book"] is False
    assert item["included_chapters"] == []
    assert item["created_at"].endswith(UTC_SUFFIXES)
    assert item["session_created_at"].endswith(UTC_SUFFIXES)


async def test_mobile_memoir_voice_transcripts_use_source_card_summary(client, test_db):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="爷爷退休后还在帮公司做事，大家都很信任他。",
    )
    await add_ready_source_card(
        test_db,
        created,
        short_title="爷爷退休后仍帮公司做事",
        summary="讲述者提到爷爷退休后仍继续帮公司处理事务，说明大家信任他。",
    )

    list_response = await client.get("/api/mobile/memoir/voice-transcripts")
    detail_response = await client.get(f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}")

    assert list_response.status_code == 200
    item = list_response.json()["items"][0]
    assert item["summary_title"] == "爷爷退休后仍帮公司做事"
    assert item["summary_text"] == "讲述者提到爷爷退休后仍继续帮公司处理事务，说明大家信任他。"
    assert item["summary_source"] == "source_card"

    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["id"] == created["utterance_id"]
    assert detail["summary_title"] == item["summary_title"]
    assert detail["summary_text"] == item["summary_text"]
    assert detail["summary_source"] == "source_card"


async def test_mobile_memoir_voice_transcripts_ignore_stale_source_card_summary(client, test_db):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="小时候院子里的葡萄架夏天会遮出一大片阴凉。",
    )
    await add_ready_source_card(
        test_db,
        created,
        short_title="过期标题不应显示",
        summary="这条摘要来自旧文本。",
        source_text="这是一段已经过期的旧文本。",
    )

    response = await client.get("/api/mobile/memoir/voice-transcripts")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["summary_title"] == "小时候院子里的葡萄架夏天会遮出一大片阴凉。"
    assert item["summary_text"] == "小时候院子里的葡萄架夏天会遮出一大片阴凉。"
    assert item["summary_source"] == "fallback"


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

    route_job = await test_db.scalar(
        select(MemoirEditingJob).where(MemoirEditingJob.dedupe_key == f"route_source:{created['utterance_id']}")
    )
    assert route_job is not None
    assert route_job.job_type == MemoirEditingJobType.ROUTE_SOURCES.value
    assert route_job.payload_json["source"] == "transcript_user_edited"
    assert route_job.payload_json[ROUTE_PAYLOAD_TRANSCRIPT_VERSION_IDS_KEY] == {
        created["utterance_id"]: versions[-1].id,
    }
    assert route_job.payload_json[ROUTE_PAYLOAD_SOURCE_TEXT_HASHES_KEY] == {
        created["utterance_id"]: source_text_hash("我小时候住在外婆家。"),
    }


async def test_mobile_memoir_editing_included_transcript_marks_chapter_for_revision(client, test_db):
    created = await create_test_voice_transcript(client, test_db)
    project_id = created["session"]["project_id"]
    plan = MemoirBookPlan(
        project_id=project_id,
        title="外婆回忆录",
        status=MemoirBookPlanStatus.CONFIRMED.value,
        created_by=MemoirCreatedBy.USER.value,
    )
    test_db.add(plan)
    await test_db.flush()
    chapter = MemoirBookPlanChapter(
        plan_id=plan.id,
        project_id=project_id,
        title="外婆家的童年",
        sort_order=0,
    )
    test_db.add(chapter)
    await test_db.flush()
    source_link = _chapter_source_link(
        project_id=project_id,
        plan_id=plan.id,
        chapter_id=chapter.id,
        created=created,
        quote="嗯，我小时候就是住在外婆家。",
    )
    test_db.add(source_link)
    draft = MemoirBookChapterDraft(
        project_id=project_id,
        chapter_id=chapter.id,
        status=MemoirBookChapterDraftStatus.CONFIRMED.value,
    )
    test_db.add(draft)
    await test_db.flush()
    draft_version = MemoirBookChapterDraftVersion(
        draft_id=draft.id,
        project_id=project_id,
        chapter_id=chapter.id,
        version=1,
        content="我小时候住在外婆家。",
        created_by=MemoirCreatedBy.AI.value,
    )
    test_db.add(draft_version)
    await test_db.flush()
    draft_version_id = draft_version.id
    source_link.merged_draft_version_id = draft_version_id
    test_db.add(source_link)
    await test_db.commit()

    response = await client.patch(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}",
        json={"transcript_edited": "我小时候住在外婆家的院子里。"},
    )

    assert response.status_code == 200
    version = await test_db.scalar(
        select(MemoirTranscriptVersion)
        .where(
            MemoirTranscriptVersion.source_artifact_id == created["utterance_id"],
            MemoirTranscriptVersion.version_type == MemoirTranscriptVersionType.USER_EDITED.value,
        )
        .order_by(MemoirTranscriptVersion.created_at.desc(), MemoirTranscriptVersion.id.desc())
    )
    await test_db.refresh(chapter)
    await test_db.refresh(draft)
    link = await test_db.scalar(
        select(MemoirChapterSourceLink).where(
            MemoirChapterSourceLink.project_id == project_id,
            MemoirChapterSourceLink.chapter_id == chapter.id,
            MemoirChapterSourceLink.utterance_id == created["utterance_id"],
        )
    )
    assert link is not None
    assert link.transcript_version_id == version.id
    assert link.quote == "我小时候住在外婆家的院子里。"
    assert chapter.metadata_json["pending_source_utterance_ids"] == [created["utterance_id"]]
    assert draft.status == MemoirBookChapterDraftStatus.DRAFT.value
    assert draft.metadata_json["status_reason"] == "sources_changed"

    route_job = await test_db.scalar(
        select(MemoirEditingJob).where(MemoirEditingJob.dedupe_key == f"route_source:{created['utterance_id']}")
    )
    assert route_job is None
    revision_job = await test_db.scalar(
        select(MemoirEditingJob).where(MemoirEditingJob.dedupe_key == f"revise_chapter:{chapter.id}")
    )
    assert revision_job is not None
    assert revision_job.job_type == MemoirEditingJobType.REVISE_CHAPTER.value
    assert revision_job.status == MemoirEditingJobStatus.PENDING.value
    assert revision_job.payload_json["base_version_id"] == draft_version_id
    assert revision_job.payload_json["pending_source_utterance_ids"] == [created["utterance_id"]]
    source_card_job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.dedupe_key == f"generate_source_card:{created['utterance_id']}"
        )
    )
    assert source_card_job is not None
    assert source_card_job.job_type == MemoirEditingJobType.GENERATE_SOURCE_CARD.value
    assert source_card_job.payload_json["utterance_id"] == created["utterance_id"]
    assert source_card_job.payload_json["reason"] == "transcript_user_edited"


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


async def test_mobile_memoir_delete_transcript_hides_related_automation_events(client, test_db):
    deleted_source = await create_test_voice_transcript(
        client,
        test_db,
        text="我小时候常在外婆家的院子里晒太阳。",
    )
    kept_source = await create_test_voice_transcript(
        client,
        test_db,
        text="后来我第一次离家去县城读书。",
    )
    project_id = deleted_source["session"]["project_id"]
    held_event = MemoirAutomationEvent(
        project_id=project_id,
        event_type=MemoirAutomationEventType.SOURCE_HELD.value,
        status=MemoirAutomationEventStatus.NEEDS_REVIEW.value,
        utterance_ids_json=[deleted_source["utterance_id"]],
        summary_text="1 条讲述需要确认归属。",
        metadata_json={
            "route": {
                "utterance_id": deleted_source["utterance_id"],
                "action": "hold_for_review",
                "reason": "需要人工确认",
            }
        },
    )
    assigned_event = MemoirAutomationEvent(
        project_id=project_id,
        event_type=MemoirAutomationEventType.SOURCE_ASSIGNED.value,
        status=MemoirAutomationEventStatus.COMPLETED.value,
        utterance_ids_json=[kept_source["utterance_id"]],
        summary_text="1 条讲述已自动整理进「离家读书」。",
        metadata_json={"routes": [{"utterance_id": kept_source["utterance_id"]}]},
    )
    test_db.add_all([held_event, assigned_event])
    await test_db.commit()

    response = await client.delete(f"/api/mobile/memoir/voice-transcripts/{deleted_source['utterance_id']}")

    assert response.status_code == 204
    refreshed_held = await test_db.scalar(
        select(MemoirAutomationEvent).where(MemoirAutomationEvent.id == held_event.id)
    )
    assert refreshed_held.status == MemoirAutomationEventStatus.COMPLETED.value
    assert refreshed_held.utterance_ids_json == []
    assert refreshed_held.metadata_json["resolution"] == "ignored"
    assert refreshed_held.metadata_json["resolution_reason"] == "source_deleted"
    assert refreshed_held.metadata_json["deleted_utterance_ids"] == [deleted_source["utterance_id"]]

    events_response = await client.get(f"/api/mobile/memoir/projects/{project_id}/automation-events")
    assert events_response.status_code == 200
    event_data = events_response.json()
    assert event_data["total"] == 1
    assert event_data["items"][0]["id"] == assigned_event.id


async def test_cleanup_dangling_automation_events_hides_deleted_sources_and_keeps_valid_refs(client, test_db):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="我小时候常在外婆家的院子里晒太阳。",
    )
    project_id = created["session"]["project_id"]
    deleted_utterance_id = "deleted-utterance-id"
    partial_event = MemoirAutomationEvent(
        project_id=project_id,
        event_type=MemoirAutomationEventType.SOURCE_ASSIGNED.value,
        status=MemoirAutomationEventStatus.COMPLETED.value,
        utterance_ids_json=[created["utterance_id"], deleted_utterance_id],
        summary_text="2 条讲述已自动整理进「院子里的童年」。",
        metadata_json={
            "routes": [
                {"utterance_id": created["utterance_id"], "reason": "同一主题"},
                {"utterance_id": deleted_utterance_id, "reason": "已删除"},
            ]
        },
    )
    dangling_event = MemoirAutomationEvent(
        project_id=project_id,
        event_type=MemoirAutomationEventType.SOURCE_HELD.value,
        status=MemoirAutomationEventStatus.NEEDS_REVIEW.value,
        utterance_ids_json=[deleted_utterance_id],
        summary_text="1 条讲述需要确认归属。",
        metadata_json={"route": {"utterance_id": deleted_utterance_id}},
    )
    confirmed_event = MemoirAutomationEvent(
        project_id=project_id,
        event_type=MemoirAutomationEventType.SOURCE_HELD.value,
        status=MemoirAutomationEventStatus.COMPLETED.value,
        utterance_ids_json=[deleted_utterance_id],
        summary_text="整理建议已处理。",
        metadata_json={
            "route": {"utterance_id": deleted_utterance_id},
            "resolution": "confirmed",
            "resolved_at": "2026-06-20T12:00:00+00:00",
        },
    )
    test_db.add_all([partial_event, dangling_event, confirmed_event])
    await test_db.commit()

    result = await automation_event_resolution.cleanup_dangling_automation_event_utterances(
        test_db,
        project_id=project_id,
    )
    await test_db.commit()

    assert result.updated_event_count == 3
    assert result.deleted_utterance_ids == [deleted_utterance_id]

    refreshed_partial = await test_db.scalar(
        select(MemoirAutomationEvent).where(MemoirAutomationEvent.id == partial_event.id)
    )
    assert refreshed_partial.utterance_ids_json == [created["utterance_id"]]
    assert refreshed_partial.summary_text == "1 条讲述已自动整理进「院子里的童年」。"
    assert refreshed_partial.metadata_json["deleted_utterance_ids"] == [deleted_utterance_id]
    assert refreshed_partial.metadata_json["routes"] == [
        {"utterance_id": created["utterance_id"], "reason": "同一主题"}
    ]
    assert "resolution" not in refreshed_partial.metadata_json

    refreshed_dangling = await test_db.scalar(
        select(MemoirAutomationEvent).where(MemoirAutomationEvent.id == dangling_event.id)
    )
    assert refreshed_dangling.status == MemoirAutomationEventStatus.COMPLETED.value
    assert refreshed_dangling.utterance_ids_json == []
    assert refreshed_dangling.metadata_json["resolution"] == "ignored"
    assert refreshed_dangling.metadata_json["resolution_reason"] == "source_deleted"

    refreshed_confirmed = await test_db.scalar(
        select(MemoirAutomationEvent).where(MemoirAutomationEvent.id == confirmed_event.id)
    )
    assert refreshed_confirmed.utterance_ids_json == []
    assert refreshed_confirmed.metadata_json["resolution"] == "confirmed"
    assert "resolution_reason" not in refreshed_confirmed.metadata_json

    events_response = await client.get(f"/api/mobile/memoir/projects/{project_id}/automation-events")
    assert events_response.status_code == 200
    event_data = events_response.json()
    assert event_data["total"] == 1
    assert event_data["items"][0]["id"] == partial_event.id
    assert event_data["items"][0]["utterance_ids"] == [created["utterance_id"]]


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
    chapter = MemoirBookPlanChapter(
        plan_id=plan.id,
        project_id=project_id,
        title="院子里的童年",
        sort_order=0,
    )
    test_db.add(chapter)
    await test_db.flush()
    test_db.add(
        _chapter_source_link(
            project_id=project_id,
            plan_id=plan.id,
            chapter_id=chapter.id,
            created=created,
            quote="外婆家的院子",
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
    chapter = MemoirBookPlanChapter(
        plan_id=plan.id,
        project_id=project_id,
        title="院子里的童年",
        sort_order=0,
    )
    test_db.add(chapter)
    await test_db.flush()
    test_db.add(
        _chapter_source_link(
            project_id=project_id,
            plan_id=plan.id,
            chapter_id=chapter.id,
            created=included,
            quote="外婆家的院子",
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
    )
    test_db.add(chapter)
    await test_db.flush()
    test_db.add_all([
        _chapter_source_link(
            project_id=project_id,
            plan_id=plan.id,
            chapter_id=chapter.id,
            created=first,
            quote="外婆家的院子",
        ),
        _chapter_source_link(
            project_id=project_id,
            plan_id=plan.id,
            chapter_id=chapter.id,
            created=second,
            quote="第一次离家",
        ),
    ])
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
    assert refreshed_chapter.metadata_json["removed_source_utterance_ids"] == [second["utterance_id"]]
    assert "source_refs_removed_at" in refreshed_chapter.metadata_json
    removed_link = await test_db.scalar(
        select(MemoirChapterSourceLink).where(
            MemoirChapterSourceLink.project_id == project_id,
            MemoirChapterSourceLink.chapter_id == chapter.id,
            MemoirChapterSourceLink.utterance_id == second["utterance_id"],
        )
    )
    assert removed_link is not None
    assert removed_link.status == MemoirChapterSourceLinkStatus.REMOVED.value
    assert removed_link.removed_at is not None

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

    route_job = await test_db.scalar(
        select(MemoirEditingJob).where(MemoirEditingJob.dedupe_key == f"route_source:{created['utterance_id']}")
    )
    assert route_job is not None
    assert route_job.payload_json["source"] == "transcript_dialect_corrected"
    assert route_job.payload_json["ai_model"] == "deepseek"
    assert route_job.payload_json[ROUTE_PAYLOAD_TRANSCRIPT_VERSION_IDS_KEY] == {
        created["utterance_id"]: version.id,
    }
    source_card_job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.dedupe_key == f"generate_source_card:{created['utterance_id']}"
        )
    )
    assert source_card_job is not None
    assert source_card_job.job_type == MemoirEditingJobType.GENERATE_SOURCE_CARD.value
    assert source_card_job.payload_json["utterance_id"] == created["utterance_id"]
    assert source_card_job.payload_json["reason"] == "transcript_dialect_corrected"
    assert source_card_job.payload_json["ai_model"] == "deepseek"


async def test_mobile_memoir_voice_edit_preview_uses_ai_without_saving(client, test_db, monkeypatch):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="我小时候住在外婆家。",
    )
    ai_client = CapturingAIClient(
        json.dumps(
            {
                "action": "revise_transcript",
                "edited_text": "我小时候住在奶奶家。",
                "changes": [
                    {"before": "外婆", "after": "奶奶", "reason": "用户明确说人物识别错了"}
                ],
                "confidence": 0.93,
                "reply_text": "已把外婆改成奶奶，请确认后保存。",
            },
            ensure_ascii=False,
        )
    )
    monkeypatch.setattr(
        "app.services.memoir.transcript_voice_edit.get_ai_service",
        lambda model=None: ai_client,
    )
    utterance_before = await test_db.scalar(
        select(MemoirVoiceUtterance).where(MemoirVoiceUtterance.id == created["utterance_id"])
    )
    message_before = await test_db.scalar(select(MemoirChatMessage).where(MemoirChatMessage.id == created["message_id"]))
    transcript_edited_before = utterance_before.transcript_edited
    message_text_before = message_before.content_text

    response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/voice-edit/preview",
        json={"instruction_text": "这里不是外婆，是奶奶", "ai_model": "deepseek"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["utterance_id"] == created["utterance_id"]
    assert data["action"] == "revise_transcript"
    assert data["original_text"] == "我小时候住在外婆家。"
    assert data["current_text"] == "我小时候住在外婆家。"
    assert data["edited_text"] == "我小时候住在奶奶家。"
    assert data["changes"] == [
        {"before": "外婆", "after": "奶奶", "reason": "用户明确说人物识别错了"}
    ]
    assert data["confidence"] == 0.93
    assert data["requires_confirmation"] is True
    assert data["provider"] == "fake-ai"
    assert data["model"] == "fake-cleaner"
    assert "只能改用户修正指令明确指出" in ai_client.prompt
    assert "当前整理文字：我小时候住在外婆家。" in ai_client.prompt
    assert "用户语音修正指令：\n这里不是外婆，是奶奶" in ai_client.prompt

    utterance = await test_db.scalar(
        select(MemoirVoiceUtterance).where(MemoirVoiceUtterance.id == created["utterance_id"])
    )
    message = await test_db.scalar(select(MemoirChatMessage).where(MemoirChatMessage.id == created["message_id"]))
    user_versions = (
        await test_db.execute(
            select(MemoirTranscriptVersion).where(
                MemoirTranscriptVersion.source_artifact_id == created["utterance_id"],
                MemoirTranscriptVersion.version_type == MemoirTranscriptVersionType.USER_EDITED.value,
            )
        )
    ).scalars().all()
    assert utterance.transcript_edited == transcript_edited_before
    assert message.content_text == message_text_before
    assert user_versions == []


async def test_mobile_memoir_voice_edit_preview_rejects_invalid_ai_payload(client, test_db, monkeypatch):
    created = await create_test_voice_transcript(client, test_db)
    monkeypatch.setattr(
        "app.services.memoir.transcript_voice_edit.get_ai_service",
        lambda model=None: CapturingAIClient("不是 JSON"),
    )

    response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/voice-edit/preview",
        json={"instruction_text": "把外婆改成奶奶"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "AI 未返回可用修正预览"


async def test_mobile_memoir_voice_edit_preview_rejects_overlong_ai_result(client, test_db, monkeypatch):
    created = await create_test_voice_transcript(client, test_db, text="我小时候住在外婆家。")
    monkeypatch.setattr(
        "app.services.memoir.transcript_voice_edit.get_ai_service",
        lambda model=None: CapturingAIClient(
            json.dumps(
                {
                    "action": "revise_transcript",
                    "edited_text": "这是一段被错误扩写的内容。" * 80,
                    "changes": [],
                    "confidence": 0.4,
                    "reply_text": "已修正。",
                },
                ensure_ascii=False,
            )
        ),
    )

    response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/voice-edit/preview",
        json={"instruction_text": "这里不是外婆，是奶奶"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "AI 修正结果过长，请重新说明要改哪一处"


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
    assert normalize_memoir_asr_text("第一张和第二张换一下。") == "第一章和第二章换一下。"
    assert normalize_memoir_asr_text("现在有几张啊？") == "现在有几章啊？"
    assert normalize_memoir_asr_text("张杰这个人，在书里单独占了一章。") == "张杰这个人，在书里单独占了一章。"


def test_memoir_asr_correction_rule_files_stay_in_sync():
    repo_root = Path(__file__).resolve().parents[2]
    backend_rules = json.loads(
        (repo_root / "backend/app/services/memoir/asr_correction_rules.json").read_text(encoding="utf-8")
    )
    miniprogram_rules = json.loads(
        (repo_root / "miniprogram/services/asr-correction-rules.json").read_text(encoding="utf-8")
    )

    assert backend_rules == miniprogram_rules


async def test_generate_cleaned_transcript_text_uses_fallback_when_not_strict():
    result = await generate_cleaned_transcript_text(
        "那个就是今天下雨了，那个那个那个雨下的很大。",
        ai_client=FailingAIClient(),
        strict=False,
    )

    assert result.content == "今天下雨了，雨下的很大。"
    assert result.provider is None
    assert result.created_by == MemoirCreatedBy.SYSTEM


async def test_generate_cleaned_transcript_text_sends_recent_context_to_llm():
    ai_client = CapturingAIClient()
    result = await generate_cleaned_transcript_text(
        "现在有几个张杰啊？",
        ai_client=ai_client,
        context=MemoirTranscriptCleanupContext(
            scene="voice_chat",
            guidance_mode="guided_interview",
            current_guidance_module="family",
            recent_user_utterances=("我刚才说到第一章。",),
            recent_assistant_messages=("我们继续聊这本书的章节安排。",),
        ),
    )

    assert result.content == "现在有几个章节啊？"
    assert "上下文只用于校对判断" in ai_client.prompt
    assert "场景：voice_chat" in ai_client.prompt
    assert "引导模式：guided_interview" in ai_client.prompt
    assert "当前引导模块：family" in ai_client.prompt
    assert "我刚才说到第一章。" in ai_client.prompt
    assert "我们继续聊这本书的章节安排。" in ai_client.prompt


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
